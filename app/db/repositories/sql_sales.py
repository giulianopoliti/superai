from datetime import UTC, date, datetime, time
from decimal import Decimal

from sqlalchemy import and_, case, func, not_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    ProductFamilyModel,
    ProductModel,
    ProductSalesDailyModel,
    SalesImportModel,
    SalesItemLineModel,
)
from app.modules.sales.schemas import (
    ProductSalesDaily,
    ProductSalesSummary,
    SalesHistoryStatus,
    SalesImportResult,
    SalesImportStatus,
    SalesItemLine,
)

POSTGRES_UPSERT_BATCH_SIZE = 100
POSTGRES_LINE_INSERT_BATCH_SIZE = 500


class SqlSalesRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def add_sales_import(self, sales_import: SalesImportResult) -> SalesImportResult:
        with self._session_factory() as session:
            model = SalesImportModel(
                id=sales_import.id,
                business_id=sales_import.business_id,
                source_filename=sales_import.source_filename,
                source_type=sales_import.source_type,
                period_start=self._date_to_datetime(sales_import.period_start),
                period_end=self._date_to_datetime(sales_import.period_end),
                row_count=sales_import.row_count,
                imported_count=sales_import.imported_count,
                skipped_count=sales_import.skipped_count,
                unique_ticket_count=sales_import.unique_ticket_count,
                unique_product_count=sales_import.unique_product_count,
                status=sales_import.status,
                errors=sales_import.errors,
                summary=sales_import.summary,
                created_at=sales_import.created_at,
                completed_at=sales_import.completed_at,
            )
            session.add(model)
            session.commit()
            session.refresh(model)
            return self._to_sales_import(model)

    def update_sales_import(self, sales_import: SalesImportResult) -> SalesImportResult:
        with self._session_factory() as session:
            model = session.get(SalesImportModel, sales_import.id)
            if model is None:
                raise ValueError(f"Sales import not found: {sales_import.id}")

            model.row_count = sales_import.row_count
            model.imported_count = sales_import.imported_count
            model.skipped_count = sales_import.skipped_count
            model.unique_ticket_count = sales_import.unique_ticket_count
            model.unique_product_count = sales_import.unique_product_count
            model.status = sales_import.status
            model.errors = sales_import.errors
            model.summary = sales_import.summary
            model.completed_at = sales_import.completed_at
            session.commit()
            session.refresh(model)
            return self._to_sales_import(model)

    def resolve_product_ids(
        self,
        *,
        business_id: str,
        external_product_ids: set[str],
    ) -> dict[str, str]:
        if not external_product_ids:
            return {}

        with self._session_factory() as session:
            rows = session.execute(
                select(ProductModel.external_product_id, ProductModel.id).where(
                    ProductModel.business_id == business_id,
                    ProductModel.external_product_id.in_(external_product_ids),
                )
            ).all()
            return {
                str(external_product_id): str(product_id)
                for external_product_id, product_id in rows
                if external_product_id
            }

    def upsert_product_sales_daily(
        self,
        *,
        records: list[ProductSalesDaily],
    ) -> list[ProductSalesDaily]:
        if not records:
            return []

        business_ids = {record.business_id for record in records}
        if len(business_ids) != 1:
            raise ValueError("Bulk sales upsert must be scoped to one business_id.")
        business_id = records[0].business_id
        external_product_ids = {record.external_product_id for record in records}
        sale_dates = [self._date_to_datetime(record.sale_date) for record in records]

        with self._session_factory() as session:
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                for batch in self._chunks(records, POSTGRES_UPSERT_BATCH_SIZE):
                    self._upsert_product_sales_daily_postgres(session=session, records=batch)
                return records

            existing_models = session.scalars(
                select(ProductSalesDailyModel).where(
                    ProductSalesDailyModel.business_id == business_id,
                    ProductSalesDailyModel.external_product_id.in_(external_product_ids),
                    ProductSalesDailyModel.sale_date >= min(sale_dates),
                    ProductSalesDailyModel.sale_date <= max(sale_dates),
                )
            ).all()
            existing_by_key = {
                (model.external_product_id, model.sale_date.date()): model
                for model in existing_models
            }
            models: list[ProductSalesDailyModel] = []
            for record in records:
                sale_datetime = self._date_to_datetime(record.sale_date)
                model = existing_by_key.get((record.external_product_id, record.sale_date))
                if model is None:
                    model = ProductSalesDailyModel(
                        id=record.id,
                        business_id=record.business_id,
                        external_product_id=record.external_product_id,
                        sale_date=sale_datetime,
                    )
                    session.add(model)
                    existing_by_key[(record.external_product_id, record.sale_date)] = model

                model.source_import_id = record.source_import_id
                model.product_id = record.product_id
                model.sku = record.sku
                model.quantity_sold = record.quantity_sold
                model.revenue = record.revenue
                model.cost_amount = record.cost_amount
                model.gross_margin = record.gross_margin
                model.ticket_count = record.ticket_count
                model.line_count = record.line_count
                model.zero_cost_line_count = record.zero_cost_line_count
                model.updated_at = record.updated_at
                models.append(model)

            session.commit()
            return [self._to_product_sales_daily(model) for model in models]

    def insert_sales_item_lines(
        self,
        *,
        lines: list[SalesItemLine],
    ) -> int:
        if not lines:
            return 0

        business_ids = {line.business_id for line in lines}
        if len(business_ids) != 1:
            raise ValueError("Bulk sales line insert must be scoped to one business_id.")

        with self._session_factory() as session:
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                inserted_count = 0
                for batch in self._chunks(lines, POSTGRES_LINE_INSERT_BATCH_SIZE):
                    inserted_count += self._insert_sales_item_lines_postgres(
                        session=session,
                        lines=batch,
                    )
                return inserted_count

            existing_ids = self._existing_sale_item_ids(session=session, lines=lines)
            new_lines = [line for line in lines if line.external_sale_item_id not in existing_ids]
            session.add_all(self._line_to_model(line) for line in new_lines)
            session.commit()
            return len(new_lines)

    def rebuild_product_sales_daily(
        self,
        *,
        business_id: str,
        source_import_id: str,
        date_from: date,
        date_to: date,
    ) -> list[ProductSalesDaily]:
        with self._session_factory() as session:
            rows = session.execute(
                select(
                    SalesItemLineModel.product_id,
                    SalesItemLineModel.external_product_id,
                    SalesItemLineModel.sku,
                    SalesItemLineModel.sale_date,
                    func.coalesce(func.sum(SalesItemLineModel.quantity), 0),
                    func.coalesce(func.sum(SalesItemLineModel.revenue), 0),
                    func.coalesce(func.sum(SalesItemLineModel.cost_amount), 0),
                    func.coalesce(func.sum(SalesItemLineModel.gross_margin), 0),
                    func.count(func.distinct(SalesItemLineModel.external_sale_id)),
                    func.count(SalesItemLineModel.id),
                    func.coalesce(
                        func.sum(case((SalesItemLineModel.unit_cost == 0, 1), else_=0)),
                        0,
                    ),
                )
                .where(
                    SalesItemLineModel.business_id == business_id,
                    SalesItemLineModel.sale_date >= self._date_to_datetime(date_from),
                    SalesItemLineModel.sale_date <= self._date_to_datetime(date_to),
                )
                .group_by(
                    SalesItemLineModel.product_id,
                    SalesItemLineModel.external_product_id,
                    SalesItemLineModel.sku,
                    SalesItemLineModel.sale_date,
                )
            ).all()

        records = [
            ProductSalesDaily(
                business_id=business_id,
                source_import_id=source_import_id,
                product_id=product_id,
                external_product_id=external_product_id,
                sku=sku,
                sale_date=sale_datetime.date(),
                quantity_sold=quantity_sold,
                revenue=revenue,
                cost_amount=cost_amount,
                gross_margin=gross_margin,
                ticket_count=ticket_count,
                line_count=line_count,
                zero_cost_line_count=zero_cost_line_count,
            )
            for (
                product_id,
                external_product_id,
                sku,
                sale_datetime,
                quantity_sold,
                revenue,
                cost_amount,
                gross_margin,
                ticket_count,
                line_count,
                zero_cost_line_count,
            ) in rows
        ]
        return self.upsert_product_sales_daily(records=records)

    def get_sales_history_status(self, *, business_id: str) -> SalesHistoryStatus:
        with self._session_factory() as session:
            sales_row = session.execute(
                select(
                    func.min(SalesItemLineModel.sale_date),
                    func.max(SalesItemLineModel.sale_date),
                    func.count(SalesItemLineModel.id),
                ).where(SalesItemLineModel.business_id == business_id)
            ).one()
            daily_count = session.execute(
                select(func.count(ProductSalesDailyModel.id)).where(
                    ProductSalesDailyModel.business_id == business_id
                )
            ).scalar_one()
            latest_import = session.execute(
                select(SalesImportModel.source_filename, SalesImportModel.completed_at)
                .where(
                    SalesImportModel.business_id == business_id,
                    SalesImportModel.status == SalesImportStatus.COMPLETED.value,
                )
                .order_by(SalesImportModel.completed_at.desc())
                .limit(1)
            ).one_or_none()

        first_sale_date, last_sale_date, total_sale_lines = sales_row
        return SalesHistoryStatus(
            business_id=business_id,
            first_sale_date=first_sale_date.date() if first_sale_date else None,
            last_sale_date=last_sale_date.date() if last_sale_date else None,
            last_imported_at=latest_import[1] if latest_import else None,
            last_import_filename=latest_import[0] if latest_import else None,
            total_sale_lines=total_sale_lines,
            total_daily_records=daily_count,
        )

    def _insert_sales_item_lines_postgres(
        self,
        *,
        session: Session,
        lines: list[SalesItemLine],
    ) -> int:
        statement = pg_insert(SalesItemLineModel).values(
            [self._line_to_values(line) for line in lines]
        )
        statement = statement.on_conflict_do_nothing(
            constraint="uq_sales_item_lines_business_external_item"
        ).returning(SalesItemLineModel.id)
        result = session.execute(statement)
        inserted_ids = result.scalars().all()
        session.commit()
        return len(inserted_ids)

    @staticmethod
    def _existing_sale_item_ids(
        *,
        session: Session,
        lines: list[SalesItemLine],
    ) -> set[str]:
        business_id = lines[0].business_id
        external_sale_item_ids = {line.external_sale_item_id for line in lines}
        existing = session.scalars(
            select(SalesItemLineModel.external_sale_item_id).where(
                SalesItemLineModel.business_id == business_id,
                SalesItemLineModel.external_sale_item_id.in_(external_sale_item_ids),
            )
        ).all()
        return set(existing)

    def _line_to_model(self, line: SalesItemLine) -> SalesItemLineModel:
        return SalesItemLineModel(**self._line_to_values(line))

    def _line_to_values(self, line: SalesItemLine) -> dict[str, object]:
        return {
            "id": line.id,
            "business_id": line.business_id,
            "source_import_id": line.source_import_id,
            "product_id": line.product_id,
            "external_sale_item_id": line.external_sale_item_id,
            "external_sale_id": line.external_sale_id,
            "external_product_id": line.external_product_id,
            "sku": line.sku,
            "sold_at": line.sold_at,
            "sale_date": self._date_to_datetime(line.sale_date),
            "quantity": line.quantity,
            "revenue": line.revenue,
            "unit_cost": line.unit_cost,
            "cost_amount": line.cost_amount,
            "gross_margin": line.gross_margin,
            "raw_payload": line.raw_payload,
            "created_at": line.created_at,
        }

    def _upsert_product_sales_daily_postgres(
        self,
        *,
        session: Session,
        records: list[ProductSalesDaily],
    ) -> None:
        now = datetime.now(UTC)
        rows = [
            {
                "id": record.id,
                "business_id": record.business_id,
                "source_import_id": record.source_import_id,
                "product_id": record.product_id,
                "external_product_id": record.external_product_id,
                "sku": record.sku,
                "sale_date": self._date_to_datetime(record.sale_date),
                "quantity_sold": record.quantity_sold,
                "revenue": record.revenue,
                "cost_amount": record.cost_amount,
                "gross_margin": record.gross_margin,
                "ticket_count": record.ticket_count,
                "line_count": record.line_count,
                "zero_cost_line_count": record.zero_cost_line_count,
                "created_at": record.created_at,
                "updated_at": now,
            }
            for record in records
        ]
        statement = pg_insert(ProductSalesDailyModel).values(rows)
        excluded = statement.excluded
        statement = statement.on_conflict_do_update(
            constraint="uq_product_sales_daily_business_product_date",
            set_={
                "source_import_id": excluded.source_import_id,
                "product_id": excluded.product_id,
                "sku": excluded.sku,
                "quantity_sold": excluded.quantity_sold,
                "revenue": excluded.revenue,
                "cost_amount": excluded.cost_amount,
                "gross_margin": excluded.gross_margin,
                "ticket_count": excluded.ticket_count,
                "line_count": excluded.line_count,
                "zero_cost_line_count": excluded.zero_cost_line_count,
                "updated_at": excluded.updated_at,
            },
        )
        session.execute(statement)
        session.commit()

    @staticmethod
    def _chunks(
        records: list[ProductSalesDaily],
        batch_size: int,
    ) -> list[list[ProductSalesDaily]]:
        return [records[index : index + batch_size] for index in range(0, len(records), batch_size)]

    def summarize_product_sales(
        self,
        *,
        business_id: str,
        product_id: str,
        date_from: date,
        date_to: date,
    ) -> ProductSalesSummary | None:
        with self._session_factory() as session:
            product = session.get(ProductModel, product_id)
            if product is None or product.business_id != business_id:
                return None

            row = session.execute(
                select(
                    func.coalesce(func.sum(ProductSalesDailyModel.quantity_sold), 0),
                    func.coalesce(func.sum(ProductSalesDailyModel.revenue), 0),
                    func.coalesce(func.sum(ProductSalesDailyModel.cost_amount), 0),
                    func.coalesce(func.sum(ProductSalesDailyModel.gross_margin), 0),
                    func.coalesce(func.sum(ProductSalesDailyModel.ticket_count), 0),
                    func.coalesce(func.sum(ProductSalesDailyModel.line_count), 0),
                    func.coalesce(func.sum(ProductSalesDailyModel.zero_cost_line_count), 0),
                ).where(
                    ProductSalesDailyModel.business_id == business_id,
                    ProductSalesDailyModel.product_id == product_id,
                    ProductSalesDailyModel.sale_date >= self._date_to_datetime(date_from),
                    ProductSalesDailyModel.sale_date <= self._date_to_datetime(date_to),
                )
            ).one()
            return self._summary_from_row(
                business_id=business_id,
                date_from=date_from,
                date_to=date_to,
                row=row,
                product=product,
            )

    def summarize_products_sales(
        self,
        *,
        business_id: str,
        product_ids: set[str],
        date_from: date,
        date_to: date,
    ) -> dict[str, ProductSalesSummary]:
        if not product_ids:
            return {}

        with self._session_factory() as session:
            products = session.scalars(
                select(ProductModel).where(
                    ProductModel.business_id == business_id,
                    ProductModel.id.in_(product_ids),
                )
            ).all()
            aggregate_rows = session.execute(
                select(
                    ProductSalesDailyModel.product_id,
                    func.coalesce(func.sum(ProductSalesDailyModel.quantity_sold), 0),
                    func.coalesce(func.sum(ProductSalesDailyModel.revenue), 0),
                    func.coalesce(func.sum(ProductSalesDailyModel.cost_amount), 0),
                    func.coalesce(func.sum(ProductSalesDailyModel.gross_margin), 0),
                    func.coalesce(func.sum(ProductSalesDailyModel.ticket_count), 0),
                    func.coalesce(func.sum(ProductSalesDailyModel.line_count), 0),
                    func.coalesce(func.sum(ProductSalesDailyModel.zero_cost_line_count), 0),
                )
                .where(
                    ProductSalesDailyModel.business_id == business_id,
                    ProductSalesDailyModel.product_id.in_(product_ids),
                    ProductSalesDailyModel.sale_date >= self._date_to_datetime(date_from),
                    ProductSalesDailyModel.sale_date <= self._date_to_datetime(date_to),
                )
                .group_by(ProductSalesDailyModel.product_id)
            ).all()

        aggregates_by_product_id = {row[0]: row[1:] for row in aggregate_rows}
        zero_row = (0, 0, 0, 0, 0, 0, 0)
        return {
            product.id: self._summary_from_row(
                business_id=business_id,
                date_from=date_from,
                date_to=date_to,
                row=aggregates_by_product_id.get(product.id, zero_row),
                product=product,
            )
            for product in products
        }

    def top_products(
        self,
        *,
        business_id: str,
        date_from: date,
        date_to: date,
        sort: str,
        limit: int,
        exclude_product_ids: set[str] | None = None,
        exclude_product_name_terms: list[str] | None = None,
        exclude_skus: set[str] | None = None,
        category: str | None = None,
        categories: list[str] | None = None,
        exclude_categories: list[str] | None = None,
        min_quantity: Decimal | None = None,
    ) -> list[ProductSalesSummary]:
        sort_columns = {
            "quantity": func.sum(ProductSalesDailyModel.quantity_sold),
            "revenue": func.sum(ProductSalesDailyModel.revenue),
            "margin": func.sum(ProductSalesDailyModel.gross_margin),
        }
        sort_column = sort_columns.get(sort, sort_columns["quantity"])
        quantity_sum = func.coalesce(func.sum(ProductSalesDailyModel.quantity_sold), 0)
        statement = (
            select(
                ProductSalesDailyModel.product_id,
                ProductSalesDailyModel.external_product_id,
                ProductSalesDailyModel.sku,
                quantity_sum,
                func.coalesce(func.sum(ProductSalesDailyModel.revenue), 0),
                func.coalesce(func.sum(ProductSalesDailyModel.cost_amount), 0),
                func.coalesce(func.sum(ProductSalesDailyModel.gross_margin), 0),
                func.coalesce(func.sum(ProductSalesDailyModel.ticket_count), 0),
                func.coalesce(func.sum(ProductSalesDailyModel.line_count), 0),
                func.coalesce(func.sum(ProductSalesDailyModel.zero_cost_line_count), 0),
                ProductModel.name,
                ProductModel.stock_quantity,
                ProductModel.current_cost,
                ProductModel.sale_price,
                ProductModel.unit,
            )
            .outerjoin(
                ProductModel,
                and_(
                    ProductModel.id == ProductSalesDailyModel.product_id,
                    ProductModel.business_id == ProductSalesDailyModel.business_id,
                ),
            )
            .outerjoin(
                ProductFamilyModel,
                and_(
                    ProductFamilyModel.id == ProductModel.family_id,
                    ProductFamilyModel.business_id == ProductModel.business_id,
                ),
            )
            .where(
                ProductSalesDailyModel.business_id == business_id,
                ProductSalesDailyModel.sale_date >= self._date_to_datetime(date_from),
                ProductSalesDailyModel.sale_date <= self._date_to_datetime(date_to),
            )
        )

        if exclude_product_ids:
            statement = statement.where(
                or_(
                    ProductSalesDailyModel.product_id.is_(None),
                    not_(ProductSalesDailyModel.product_id.in_(exclude_product_ids)),
                )
            )
        if exclude_skus:
            statement = statement.where(
                or_(
                    ProductSalesDailyModel.sku.is_(None),
                    not_(ProductSalesDailyModel.sku.in_(exclude_skus)),
                )
            )
        category_filters = [term.strip() for term in categories or [] if term.strip()]
        if category and not category_filters:
            category_filters = [category.strip()]
        if category_filters:
            statement = statement.where(
                or_(
                    *[
                        func.coalesce(ProductFamilyModel.name, ProductModel.category).ilike(
                            category_filter
                        )
                        for category_filter in category_filters
                    ]
                )
            )
        if exclude_categories:
            statement = statement.where(
                not_(
                    or_(
                        *[
                            func.coalesce(ProductFamilyModel.name, ProductModel.category, "").ilike(
                                term
                            )
                            for term in exclude_categories
                        ]
                    )
                )
            )
        for term in exclude_product_name_terms or []:
            cleaned_term = term.strip()
            if cleaned_term:
                pattern = f"%{cleaned_term}%"
                statement = statement.where(
                    not_(
                        ProductModel.name.ilike(pattern)
                        | ProductSalesDailyModel.external_product_id.ilike(pattern)
                        | ProductSalesDailyModel.sku.ilike(pattern)
                    )
                )

        statement = statement.group_by(
            ProductSalesDailyModel.product_id,
            ProductSalesDailyModel.external_product_id,
            ProductSalesDailyModel.sku,
            ProductModel.name,
            ProductModel.stock_quantity,
            ProductModel.current_cost,
            ProductModel.sale_price,
            ProductModel.unit,
        )
        if min_quantity is not None:
            statement = statement.having(quantity_sum >= min_quantity)
        statement = statement.order_by(
            sort_column.desc(), ProductSalesDailyModel.external_product_id
        ).limit(limit)

        with self._session_factory() as session:
            rows = session.execute(statement).all()
            return [
                ProductSalesSummary(
                    business_id=business_id,
                    product_id=product_id,
                    external_product_id=external_product_id,
                    sku=sku,
                    product_name=product_name,
                    date_from=date_from,
                    date_to=date_to,
                    days=self._days_inclusive(date_from, date_to),
                    quantity_sold=quantity_sold,
                    revenue=revenue,
                    cost_amount=cost_amount,
                    gross_margin=gross_margin,
                    ticket_count=ticket_count,
                    line_count=line_count,
                    zero_cost_line_count=zero_cost_line_count,
                    current_stock=current_stock,
                    current_cost=current_cost,
                    sale_price=sale_price,
                    unit=unit,
                )
                for (
                    product_id,
                    external_product_id,
                    sku,
                    quantity_sold,
                    revenue,
                    cost_amount,
                    gross_margin,
                    ticket_count,
                    line_count,
                    zero_cost_line_count,
                    product_name,
                    current_stock,
                    current_cost,
                    sale_price,
                    unit,
                ) in rows
            ]

    def query_sales_summary(
        self,
        *,
        business_id: str,
        date_from: date,
        date_to: date,
        categories: list[str] | None = None,
        exclude_categories: list[str] | None = None,
        exclude_product_ids: set[str] | None = None,
        exclude_skus: set[str] | None = None,
    ) -> dict[str, object]:
        # Ticket counts must use distinct POS tickets, not sums of per-product counts.
        model = SalesItemLineModel
        statement = (
            select(
                func.coalesce(func.sum(model.quantity), 0),
                func.coalesce(func.sum(model.revenue), 0),
                func.coalesce(func.sum(model.cost_amount), 0),
                func.coalesce(func.sum(model.gross_margin), 0),
                func.count(func.distinct(model.external_sale_id)),
                func.count(model.id),
                func.coalesce(func.sum(case((model.unit_cost == 0, 1), else_=0)), 0),
            )
            .outerjoin(
                ProductModel,
                and_(
                    ProductModel.id == model.product_id,
                    ProductModel.business_id == model.business_id,
                ),
            )
            .outerjoin(
                ProductFamilyModel,
                and_(
                    ProductFamilyModel.id == ProductModel.family_id,
                    ProductFamilyModel.business_id == ProductModel.business_id,
                ),
            )
            .where(
                model.business_id == business_id,
                model.sale_date >= self._date_to_datetime(date_from),
                model.sale_date <= self._date_to_datetime(date_to),
            )
        )
        if categories:
            statement = statement.where(
                or_(
                    *[
                        func.coalesce(ProductFamilyModel.name, ProductModel.category).ilike(term)
                        for term in categories
                    ]
                )
            )
        if exclude_categories:
            statement = statement.where(
                not_(
                    or_(
                        *[
                            func.coalesce(ProductFamilyModel.name, ProductModel.category, "").ilike(
                                term
                            )
                            for term in exclude_categories
                        ]
                    )
                )
            )
        if exclude_product_ids:
            statement = statement.where(
                or_(model.product_id.is_(None), not_(model.product_id.in_(exclude_product_ids)))
            )
        if exclude_skus:
            statement = statement.where(or_(model.sku.is_(None), not_(model.sku.in_(exclude_skus))))
        with self._session_factory() as session:
            row = session.execute(statement).one()
        result = dict(
            zip(
                (
                    "quantity_sold",
                    "revenue",
                    "cost_amount",
                    "gross_margin",
                    "ticket_count",
                    "line_count",
                    "zero_cost_line_count",
                ),
                row,
                strict=True,
            )
        )
        result["average_ticket"] = (
            (result["revenue"] / result["ticket_count"]).quantize(Decimal("0.01"))
            if result["ticket_count"]
            else None
        )
        result["margin_reliable"] = result["zero_cost_line_count"] == 0
        return result

    def product_sales_history(
        self,
        *,
        business_id: str,
        product_id: str,
        date_from: date,
        date_to: date,
    ) -> list[dict[str, object]]:
        model = ProductSalesDailyModel
        with self._session_factory() as session:
            rows = session.execute(
                select(
                    model.sale_date,
                    func.sum(model.quantity_sold),
                    func.sum(model.revenue),
                    func.sum(model.gross_margin),
                    func.sum(model.zero_cost_line_count),
                )
                .where(
                    model.business_id == business_id,
                    model.product_id == product_id,
                    model.sale_date >= self._date_to_datetime(date_from),
                    model.sale_date <= self._date_to_datetime(date_to),
                )
                .group_by(model.sale_date)
                .order_by(model.sale_date)
            ).all()
        return [
            dict(
                zip(
                    ("date", "quantity_sold", "revenue", "gross_margin", "zero_cost_line_count"),
                    (row[0].date().isoformat(), *row[1:]),
                    strict=True,
                )
            )
            for row in rows
        ]

    def sales_totals(
        self,
        *,
        business_id: str,
        date_from: date,
        date_to: date,
    ) -> dict[str, object]:
        with self._session_factory() as session:
            row = session.execute(
                select(
                    func.coalesce(func.sum(ProductSalesDailyModel.quantity_sold), 0),
                    func.coalesce(func.sum(ProductSalesDailyModel.revenue), 0),
                    func.coalesce(func.sum(ProductSalesDailyModel.cost_amount), 0),
                    func.coalesce(func.sum(ProductSalesDailyModel.gross_margin), 0),
                    func.coalesce(func.sum(ProductSalesDailyModel.ticket_count), 0),
                    func.coalesce(func.sum(ProductSalesDailyModel.line_count), 0),
                ).where(
                    ProductSalesDailyModel.business_id == business_id,
                    ProductSalesDailyModel.sale_date >= self._date_to_datetime(date_from),
                    ProductSalesDailyModel.sale_date <= self._date_to_datetime(date_to),
                )
            ).one()
        (
            quantity_sold,
            revenue,
            cost_amount,
            gross_margin,
            ticket_count,
            line_count,
        ) = row
        return {
            "quantity_sold": quantity_sold,
            "revenue": revenue,
            "cost_amount": cost_amount,
            "gross_margin": gross_margin,
            "ticket_count": ticket_count,
            "line_count": line_count,
        }

    @staticmethod
    def _summary_from_row(
        *,
        business_id: str,
        date_from: date,
        date_to: date,
        row,
        product: ProductModel,
    ) -> ProductSalesSummary:
        (
            quantity_sold,
            revenue,
            cost_amount,
            gross_margin,
            ticket_count,
            line_count,
            zero_cost_line_count,
        ) = row
        return ProductSalesSummary(
            business_id=business_id,
            product_id=product.id,
            external_product_id=product.external_product_id,
            sku=product.sku,
            product_name=product.name,
            unit=product.unit,
            date_from=date_from,
            date_to=date_to,
            days=SqlSalesRepository._days_inclusive(date_from, date_to),
            quantity_sold=quantity_sold,
            revenue=revenue,
            cost_amount=cost_amount,
            gross_margin=gross_margin,
            ticket_count=ticket_count,
            line_count=line_count,
            zero_cost_line_count=zero_cost_line_count,
            current_stock=product.stock_quantity,
            current_cost=product.current_cost,
            sale_price=product.sale_price,
        )

    @staticmethod
    def _to_sales_import(model: SalesImportModel) -> SalesImportResult:
        return SalesImportResult(
            id=model.id,
            business_id=model.business_id,
            source_filename=model.source_filename,
            source_type=model.source_type,
            period_start=model.period_start.date(),
            period_end=model.period_end.date(),
            row_count=model.row_count,
            imported_count=model.imported_count,
            skipped_count=model.skipped_count,
            unique_ticket_count=model.unique_ticket_count,
            unique_product_count=model.unique_product_count,
            status=SalesImportStatus(model.status),
            errors=[str(error) for error in model.errors],
            summary=model.summary,
            created_at=model.created_at,
            completed_at=model.completed_at,
        )

    @staticmethod
    def _to_product_sales_daily(model: ProductSalesDailyModel) -> ProductSalesDaily:
        return ProductSalesDaily(
            id=model.id,
            business_id=model.business_id,
            source_import_id=model.source_import_id,
            product_id=model.product_id,
            external_product_id=model.external_product_id,
            sku=model.sku,
            sale_date=model.sale_date.date(),
            quantity_sold=model.quantity_sold,
            revenue=model.revenue,
            cost_amount=model.cost_amount,
            gross_margin=model.gross_margin,
            ticket_count=model.ticket_count,
            line_count=model.line_count,
            zero_cost_line_count=model.zero_cost_line_count,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    @staticmethod
    def _date_to_datetime(value: date) -> datetime:
        return datetime.combine(value, time.min, tzinfo=UTC)

    @staticmethod
    def _days_inclusive(date_from: date, date_to: date) -> int:
        return max((date_to - date_from).days + 1, 1)
