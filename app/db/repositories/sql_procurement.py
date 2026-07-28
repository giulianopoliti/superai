from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    CatalogImportModel,
    ProductMatchCandidateModel,
    ProductMatchFeedbackModel,
    ProductModel,
    SupplierModel,
    SupplierOfferDocumentModel,
    SupplierOfferItemModel,
    SupplierProductModel,
)
from app.modules.procurement.schemas import (
    CatalogImportResult,
    CatalogImportStatus,
    Product,
    ProductMatchCandidate,
    ProductMatchCandidateRecord,
    ProductMatchCandidateStatus,
    ProductMatchFeedback,
    ProductMatchFeedbackRule,
    ProductSupplierComparison,
    ProductSupplierPrice,
    Supplier,
    SupplierOfferDocument,
    SupplierOfferDocumentStatus,
    SupplierOfferItem,
    SupplierProduct,
)


class SqlProcurementRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def add_product(self, product: Product) -> Product:
        with self._session_factory() as session:
            model = ProductModel(
                id=product.id,
                business_id=product.business_id,
                external_product_id=product.external_product_id,
                sku=product.sku,
                barcode=product.barcode,
                name=product.name,
                normalized_name=product.normalized_name,
                brand=product.brand,
                category=product.category,
                unit_size=product.unit_size,
                unit=product.unit,
                sale_price=product.sale_price,
                current_cost=product.current_cost,
                margin_percentage=product.margin_percentage,
                stock_quantity=product.stock_quantity,
                active=product.active,
                source=product.source,
                created_at=product.created_at,
                updated_at=product.updated_at,
            )
            session.add(model)
            session.commit()
            session.refresh(model)
            return self._to_product(model)

    def add_catalog_import(self, catalog_import: CatalogImportResult) -> CatalogImportResult:
        with self._session_factory() as session:
            model = CatalogImportModel(
                id=catalog_import.id,
                business_id=catalog_import.business_id,
                source_filename=catalog_import.source_filename,
                source_type=catalog_import.source_type,
                row_count=catalog_import.row_count,
                imported_count=catalog_import.imported_count,
                skipped_count=catalog_import.skipped_count,
                status=catalog_import.status,
                errors=catalog_import.errors,
                summary=catalog_import.summary,
                created_at=catalog_import.created_at,
                completed_at=catalog_import.completed_at,
            )
            session.add(model)
            session.commit()
            session.refresh(model)
            return self._to_catalog_import(model)

    def update_catalog_import(self, catalog_import: CatalogImportResult) -> CatalogImportResult:
        with self._session_factory() as session:
            model = session.get(CatalogImportModel, catalog_import.id)
            if model is None:
                raise ValueError(f"Catalog import not found: {catalog_import.id}")

            model.row_count = catalog_import.row_count
            model.imported_count = catalog_import.imported_count
            model.skipped_count = catalog_import.skipped_count
            model.status = catalog_import.status
            model.errors = catalog_import.errors
            model.summary = catalog_import.summary
            model.completed_at = catalog_import.completed_at
            session.commit()
            session.refresh(model)
            return self._to_catalog_import(model)

    def upsert_product(self, product: Product) -> Product:
        with self._session_factory() as session:
            model = self._find_existing_product(session, product)
            if model is None:
                model = ProductModel(
                    id=product.id,
                    business_id=product.business_id,
                    external_product_id=product.external_product_id,
                    sku=product.sku,
                    barcode=product.barcode,
                    name=product.name,
                    normalized_name=product.normalized_name,
                    brand=product.brand,
                    category=product.category,
                    unit_size=product.unit_size,
                    unit=product.unit,
                    sale_price=product.sale_price,
                    current_cost=product.current_cost,
                    margin_percentage=product.margin_percentage,
                    stock_quantity=product.stock_quantity,
                    active=product.active,
                    source=product.source,
                    created_at=product.created_at,
                    updated_at=product.updated_at,
                )
                session.add(model)
            else:
                model.external_product_id = product.external_product_id
                model.sku = product.sku
                model.barcode = product.barcode
                model.name = product.name
                model.normalized_name = product.normalized_name
                model.brand = product.brand
                model.category = product.category
                model.unit_size = product.unit_size
                model.unit = product.unit
                model.sale_price = product.sale_price
                model.current_cost = product.current_cost
                model.margin_percentage = product.margin_percentage
                model.stock_quantity = product.stock_quantity
                model.active = product.active
                model.source = product.source
                model.updated_at = product.updated_at

            session.commit()
            session.refresh(model)
            return self._to_product(model)

    def upsert_products(self, products: list[Product]) -> list[Product]:
        if not products:
            return []

        business_ids = {product.business_id for product in products}
        if len(business_ids) != 1:
            raise ValueError("Bulk product upsert must be scoped to one business_id.")

        business_id = products[0].business_id
        external_ids = {
            product.external_product_id for product in products if product.external_product_id
        }
        fallback_barcodes = {
            product.barcode
            for product in products
            if product.barcode and not product.external_product_id
        }

        with self._session_factory() as session:
            existing_models: list[ProductModel] = []
            if external_ids or fallback_barcodes:
                conditions = []
                if external_ids:
                    conditions.append(ProductModel.external_product_id.in_(external_ids))
                if fallback_barcodes:
                    conditions.append(ProductModel.barcode.in_(fallback_barcodes))

                existing_models = session.scalars(
                    select(ProductModel).where(
                        ProductModel.business_id == business_id,
                        or_(*conditions),
                    )
                ).all()
            by_external_id = {
                model.external_product_id: model
                for model in existing_models
                if model.external_product_id
            }
            by_barcode = {
                model.barcode: model
                for model in existing_models
                if model.barcode and not model.external_product_id
            }

            models: list[ProductModel] = []
            for product in products:
                model = None
                if product.external_product_id:
                    model = by_external_id.get(product.external_product_id)
                if model is None and product.barcode and not product.external_product_id:
                    model = by_barcode.get(product.barcode)

                if model is None:
                    model = ProductModel(id=product.id, business_id=product.business_id)
                    session.add(model)
                    if product.external_product_id:
                        by_external_id[product.external_product_id] = model
                    if product.barcode and not product.external_product_id:
                        by_barcode[product.barcode] = model

                self._apply_product(model, product)
                models.append(model)

            session.commit()
            return [self._to_product(model) for model in models]

    def count_products(self, *, business_id: str) -> int:
        with self._session_factory() as session:
            return session.scalar(
                select(func.count()).select_from(ProductModel).where(
                    ProductModel.business_id == business_id
                )
            )

    def list_products(self, *, business_id: str, active_only: bool = True) -> list[Product]:
        with self._session_factory() as session:
            statement = select(ProductModel).where(ProductModel.business_id == business_id)
            if active_only:
                statement = statement.where(ProductModel.active.is_(True))
            models = session.scalars(statement.order_by(ProductModel.name)).all()
            return [self._to_product(model) for model in models]

    def get_product(self, *, business_id: str, product_id: str) -> Product | None:
        with self._session_factory() as session:
            model = session.get(ProductModel, product_id)
            if model is None or model.business_id != business_id:
                return None
            return self._to_product(model)

    def summarize_products(self, *, business_id: str) -> dict[str, object]:
        with self._session_factory() as session:
            total_count = session.scalar(
                select(func.count()).select_from(ProductModel).where(
                    ProductModel.business_id == business_id
                )
            )
            active_count = session.scalar(
                select(func.count()).select_from(ProductModel).where(
                    ProductModel.business_id == business_id,
                    ProductModel.active.is_(True),
                )
            )
            missing_cost_count = session.scalar(
                select(func.count()).select_from(ProductModel).where(
                    ProductModel.business_id == business_id,
                    (ProductModel.current_cost.is_(None)) | (ProductModel.current_cost == 0),
                )
            )
            missing_barcode_count = session.scalar(
                select(func.count()).select_from(ProductModel).where(
                    ProductModel.business_id == business_id,
                    ProductModel.barcode.is_(None),
                )
            )
            duplicate_barcode_rows = session.execute(
                select(ProductModel.barcode, func.count())
                .where(
                    ProductModel.business_id == business_id,
                    ProductModel.barcode.is_not(None),
                )
                .group_by(ProductModel.barcode)
                .having(func.count() > 1)
            ).all()
            categories = session.execute(
                select(ProductModel.category, func.count())
                .where(ProductModel.business_id == business_id)
                .group_by(ProductModel.category)
                .order_by(func.count().desc())
            ).all()

            return {
                "total_count": total_count or 0,
                "active_count": active_count or 0,
                "missing_cost_count": missing_cost_count or 0,
                "missing_barcode_count": missing_barcode_count or 0,
                "duplicate_barcode_count": len(duplicate_barcode_rows),
                "categories": [
                    {"name": category or "UNCATEGORIZED", "count": count}
                    for category, count in categories
                ],
            }

    def add_supplier(self, supplier: Supplier) -> Supplier:
        with self._session_factory() as session:
            model = SupplierModel(
                id=supplier.id,
                business_id=supplier.business_id,
                name=supplier.name,
                normalized_name=supplier.normalized_name,
                notes=supplier.notes,
                created_at=supplier.created_at,
                updated_at=supplier.updated_at,
            )
            session.add(model)
            session.commit()
            session.refresh(model)
            return self._to_supplier(model)

    def upsert_supplier(self, supplier: Supplier) -> Supplier:
        with self._session_factory() as session:
            model = session.scalar(
                select(SupplierModel).where(
                    SupplierModel.business_id == supplier.business_id,
                    SupplierModel.normalized_name == supplier.normalized_name,
                )
            )
            if model is None:
                model = SupplierModel(
                    id=supplier.id,
                    business_id=supplier.business_id,
                    name=supplier.name,
                    normalized_name=supplier.normalized_name,
                    notes=supplier.notes,
                    created_at=supplier.created_at,
                    updated_at=supplier.updated_at,
                )
                session.add(model)
            else:
                model.name = supplier.name
                model.notes = supplier.notes
                model.updated_at = supplier.updated_at

            session.commit()
            session.refresh(model)
            return self._to_supplier(model)

    def add_supplier_offer_document(
        self, document: SupplierOfferDocument
    ) -> SupplierOfferDocument:
        with self._session_factory() as session:
            model = SupplierOfferDocumentModel(
                id=document.id,
                business_id=document.business_id,
                supplier_id=document.supplier_id,
                source_filename=document.source_filename,
                document_type=document.document_type,
                extraction_status=document.extraction_status,
                extraction_provider=document.extraction_provider,
                raw_text=document.raw_text,
                metadata_=document.metadata,
                created_at=document.created_at,
                completed_at=document.completed_at,
            )
            session.add(model)
            session.commit()
            session.refresh(model)
            return self._to_supplier_offer_document(model)

    def update_supplier_offer_document(
        self, document: SupplierOfferDocument
    ) -> SupplierOfferDocument:
        with self._session_factory() as session:
            model = session.get(SupplierOfferDocumentModel, document.id)
            if model is None:
                raise ValueError(f"Supplier offer document not found: {document.id}")

            model.extraction_status = document.extraction_status
            model.extraction_provider = document.extraction_provider
            model.raw_text = document.raw_text
            model.metadata_ = document.metadata
            model.completed_at = document.completed_at
            session.commit()
            session.refresh(model)
            return self._to_supplier_offer_document(model)

    def add_supplier_offer_items(
        self, items: list[SupplierOfferItem]
    ) -> list[SupplierOfferItem]:
        if not items:
            return []

        with self._session_factory() as session:
            models = [
                SupplierOfferItemModel(
                    id=item.id,
                    business_id=item.business_id,
                    supplier_offer_document_id=item.supplier_offer_document_id,
                    supplier_id=item.supplier_id,
                    raw_name=item.raw_name,
                    normalized_name=item.normalized_name,
                    brand=item.brand,
                    unit_size=item.unit_size,
                    unit=item.unit,
                    package_quantity=item.package_quantity,
                    offer_price=item.offer_price,
                    currency=item.currency,
                    tax_included=item.tax_included,
                    page_number=item.page_number,
                    confidence_score=item.confidence_score,
                    metadata_=item.metadata,
                    created_at=item.created_at,
                )
                for item in items
            ]
            session.add_all(models)
            session.commit()
            return [self._to_supplier_offer_item(model) for model in models]

    def list_supplier_offer_items(
        self, *, business_id: str, supplier_offer_document_id: str
    ) -> list[SupplierOfferItem]:
        with self._session_factory() as session:
            models = session.scalars(
                select(SupplierOfferItemModel)
                .where(
                    SupplierOfferItemModel.business_id == business_id,
                    SupplierOfferItemModel.supplier_offer_document_id
                    == supplier_offer_document_id,
                )
                .order_by(SupplierOfferItemModel.created_at, SupplierOfferItemModel.raw_name)
            ).all()
            return [self._to_supplier_offer_item(model) for model in models]

    def get_supplier_offer_document(
        self, *, business_id: str, supplier_offer_document_id: str
    ) -> SupplierOfferDocument | None:
        with self._session_factory() as session:
            model = session.get(SupplierOfferDocumentModel, supplier_offer_document_id)
            if model is None or model.business_id != business_id:
                return None
            return self._to_supplier_offer_document(model)

    def replace_product_match_candidates(
        self,
        *,
        business_id: str,
        supplier_offer_document_id: str,
        candidates: list[ProductMatchCandidate],
    ) -> list[ProductMatchCandidateRecord]:
        with self._session_factory() as session:
            session.execute(
                delete(ProductMatchCandidateModel).where(
                    ProductMatchCandidateModel.business_id == business_id,
                    ProductMatchCandidateModel.supplier_offer_document_id
                    == supplier_offer_document_id,
                )
            )

            records = [
                ProductMatchCandidateRecord(
                    business_id=business_id,
                    supplier_offer_document_id=supplier_offer_document_id,
                    supplier_offer_item_id=candidate.supplier_offer_item.id,
                    product_id=candidate.product.id if candidate.product else None,
                    relationship_type=candidate.relationship_type,
                    confidence_score=candidate.confidence_score,
                    reasons=candidate.reasons,
                    cost_difference=candidate.cost_difference,
                    cost_difference_percentage=candidate.cost_difference_percentage,
                    estimated_margin_percentage=candidate.estimated_margin_percentage,
                    recommendation=candidate.recommendation,
                )
                for candidate in candidates
            ]
            models = [
                ProductMatchCandidateModel(
                    id=record.id,
                    business_id=record.business_id,
                    supplier_offer_document_id=record.supplier_offer_document_id,
                    supplier_offer_item_id=record.supplier_offer_item_id,
                    product_id=record.product_id,
                    relationship_type=record.relationship_type,
                    confidence_score=record.confidence_score,
                    reasons=record.reasons,
                    cost_difference=record.cost_difference,
                    cost_difference_percentage=record.cost_difference_percentage,
                    estimated_margin_percentage=record.estimated_margin_percentage,
                    recommendation=record.recommendation,
                    status=record.status,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                )
                for record in records
            ]
            session.add_all(models)
            session.commit()
            return [self._to_product_match_candidate_record(model) for model in models]

    def list_product_match_candidates(
        self, *, business_id: str, supplier_offer_document_id: str
    ) -> list[ProductMatchCandidateRecord]:
        with self._session_factory() as session:
            models = session.scalars(
                select(ProductMatchCandidateModel)
                .where(
                    ProductMatchCandidateModel.business_id == business_id,
                    ProductMatchCandidateModel.supplier_offer_document_id
                    == supplier_offer_document_id,
                )
                .order_by(
                    ProductMatchCandidateModel.recommendation,
                    ProductMatchCandidateModel.confidence_score.desc(),
                    ProductMatchCandidateModel.created_at,
                )
            ).all()
            return [self._to_product_match_candidate_record(model) for model in models]

    def get_product_match_candidate(
        self, *, business_id: str, product_match_candidate_id: str
    ) -> ProductMatchCandidateRecord | None:
        with self._session_factory() as session:
            model = session.get(ProductMatchCandidateModel, product_match_candidate_id)
            if model is None or model.business_id != business_id:
                return None
            return self._to_product_match_candidate_record(model)

    def add_product_match_feedback(
        self, feedback: ProductMatchFeedback
    ) -> ProductMatchFeedback:
        with self._session_factory() as session:
            candidate = session.get(
                ProductMatchCandidateModel, feedback.product_match_candidate_id
            )
            if candidate is None or candidate.business_id != feedback.business_id:
                raise ValueError(
                    f"Product match candidate not found: {feedback.product_match_candidate_id}"
                )

            model = ProductMatchFeedbackModel(
                id=feedback.id,
                business_id=feedback.business_id,
                product_match_candidate_id=feedback.product_match_candidate_id,
                supplier_offer_item_id=feedback.supplier_offer_item_id,
                candidate_product_id=feedback.candidate_product_id,
                relationship_type=feedback.relationship_type,
                accepted=feedback.accepted,
                reviewed_by_user_id=feedback.reviewed_by_user_id,
                notes=feedback.notes,
                created_at=feedback.created_at,
            )
            session.add(model)
            candidate.status = (
                ProductMatchCandidateStatus.ACCEPTED
                if feedback.accepted
                else ProductMatchCandidateStatus.REJECTED
            )
            if feedback.accepted:
                candidate.product_id = feedback.candidate_product_id
                candidate.relationship_type = feedback.relationship_type
            candidate.updated_at = feedback.created_at
            session.commit()
            session.refresh(model)
            return self._to_product_match_feedback(model)

    def list_product_match_feedback_rules(
        self,
        *,
        business_id: str,
        supplier_id: str,
        normalized_names: set[str],
    ) -> list[ProductMatchFeedbackRule]:
        if not normalized_names:
            return []

        with self._session_factory() as session:
            rows = session.execute(
                select(ProductMatchFeedbackModel, SupplierOfferItemModel)
                .join(
                    SupplierOfferItemModel,
                    SupplierOfferItemModel.id
                    == ProductMatchFeedbackModel.supplier_offer_item_id,
                )
                .where(
                    ProductMatchFeedbackModel.business_id == business_id,
                    SupplierOfferItemModel.business_id == business_id,
                    SupplierOfferItemModel.supplier_id == supplier_id,
                    SupplierOfferItemModel.normalized_name.in_(normalized_names),
                )
                .order_by(ProductMatchFeedbackModel.created_at.desc())
            ).all()
            return [
                ProductMatchFeedbackRule(
                    supplier_id=item.supplier_id,
                    supplier_offer_item_normalized_name=item.normalized_name,
                    candidate_product_id=feedback.candidate_product_id,
                    relationship_type=feedback.relationship_type,
                    accepted=feedback.accepted,
                    created_at=feedback.created_at,
                )
                for feedback, item in rows
            ]

    def add_supplier_product(self, supplier_product: SupplierProduct) -> SupplierProduct:
        with self._session_factory() as session:
            model = SupplierProductModel(
                id=supplier_product.id,
                business_id=supplier_product.business_id,
                supplier_id=supplier_product.supplier_id,
                product_id=supplier_product.product_id,
                supplier_product_name=supplier_product.supplier_product_name,
                supplier_product_normalized_name=(
                    supplier_product.supplier_product_normalized_name
                ),
                cost_price=supplier_product.cost_price,
                currency=supplier_product.currency,
                tax_included=supplier_product.tax_included,
                package_quantity=supplier_product.package_quantity,
                observed_at=supplier_product.observed_at,
                metadata_=supplier_product.metadata,
                created_at=supplier_product.created_at,
                updated_at=supplier_product.updated_at,
            )
            session.add(model)
            session.commit()
            session.refresh(model)
            return self._to_supplier_product(model)

    def compare_supplier_prices(
        self, *, business_id: str, product_id: str
    ) -> ProductSupplierComparison | None:
        with self._session_factory() as session:
            product = session.get(ProductModel, product_id)
            if product is None or product.business_id != business_id:
                return None

            rows = session.execute(
                select(SupplierProductModel, SupplierModel)
                .join(SupplierModel, SupplierModel.id == SupplierProductModel.supplier_id)
                .where(
                    SupplierProductModel.business_id == business_id,
                    SupplierProductModel.product_id == product_id,
                    SupplierModel.business_id == business_id,
                )
                .order_by(SupplierProductModel.cost_price)
            ).all()

            prices = [
                ProductSupplierPrice(
                    supplier_product_id=supplier_product.id,
                    supplier_id=supplier.id,
                    supplier_name=supplier.name,
                    product_id=product.id,
                    product_name=product.name,
                    supplier_product_name=supplier_product.supplier_product_name,
                    cost_price=supplier_product.cost_price,
                    currency=supplier_product.currency,
                    observed_at=supplier_product.observed_at,
                )
                for supplier_product, supplier in rows
            ]

            return ProductSupplierComparison(
                product_id=product.id,
                product_name=product.name,
                current_cost=product.current_cost,
                sale_price=product.sale_price,
                supplier_prices=prices,
            )

    @staticmethod
    def _find_existing_product(session: Session, product: Product) -> ProductModel | None:
        if product.external_product_id:
            model = session.scalar(
                select(ProductModel).where(
                    ProductModel.business_id == product.business_id,
                    ProductModel.external_product_id == product.external_product_id,
                )
            )
            if model is not None:
                return model

        if product.barcode and not product.external_product_id:
            return session.scalar(
                select(ProductModel).where(
                    ProductModel.business_id == product.business_id,
                    ProductModel.barcode == product.barcode,
                )
            )

        return None

    @staticmethod
    def _apply_product(model: ProductModel, product: Product) -> None:
        model.external_product_id = product.external_product_id
        model.sku = product.sku
        model.barcode = product.barcode
        model.name = product.name
        model.normalized_name = product.normalized_name
        model.brand = product.brand
        model.category = product.category
        model.unit_size = product.unit_size
        model.unit = product.unit
        model.sale_price = product.sale_price
        model.current_cost = product.current_cost
        model.margin_percentage = product.margin_percentage
        model.stock_quantity = product.stock_quantity
        model.active = product.active
        model.source = product.source
        model.created_at = product.created_at
        model.updated_at = product.updated_at

    @staticmethod
    def _to_product(model: ProductModel) -> Product:
        return Product(
            id=model.id,
            business_id=model.business_id,
            external_product_id=model.external_product_id,
            sku=model.sku,
            barcode=model.barcode,
            name=model.name,
            normalized_name=model.normalized_name,
            brand=model.brand,
            category=model.category,
            unit_size=model.unit_size,
            unit=model.unit,
            sale_price=model.sale_price,
            current_cost=model.current_cost,
            margin_percentage=model.margin_percentage,
            stock_quantity=model.stock_quantity,
            active=model.active,
            source=model.source,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    @staticmethod
    def _to_catalog_import(model: CatalogImportModel) -> CatalogImportResult:
        return CatalogImportResult(
            id=model.id,
            business_id=model.business_id,
            source_filename=model.source_filename,
            source_type=model.source_type,
            row_count=model.row_count,
            imported_count=model.imported_count,
            skipped_count=model.skipped_count,
            status=CatalogImportStatus(model.status),
            errors=[str(error) for error in model.errors],
            summary=model.summary,
            created_at=model.created_at,
            completed_at=model.completed_at,
        )

    @staticmethod
    def _to_supplier(model: SupplierModel) -> Supplier:
        return Supplier(
            id=model.id,
            business_id=model.business_id,
            name=model.name,
            normalized_name=model.normalized_name,
            notes=model.notes,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    @staticmethod
    def _to_supplier_offer_document(
        model: SupplierOfferDocumentModel,
    ) -> SupplierOfferDocument:
        return SupplierOfferDocument(
            id=model.id,
            business_id=model.business_id,
            supplier_id=model.supplier_id,
            source_filename=model.source_filename,
            document_type=model.document_type,
            extraction_status=SupplierOfferDocumentStatus(model.extraction_status),
            extraction_provider=model.extraction_provider,
            raw_text=model.raw_text,
            metadata=model.metadata_,
            created_at=model.created_at,
            completed_at=model.completed_at,
        )

    @staticmethod
    def _to_supplier_offer_item(model: SupplierOfferItemModel) -> SupplierOfferItem:
        return SupplierOfferItem(
            id=model.id,
            business_id=model.business_id,
            supplier_offer_document_id=model.supplier_offer_document_id,
            supplier_id=model.supplier_id,
            raw_name=model.raw_name,
            normalized_name=model.normalized_name,
            brand=model.brand,
            unit_size=model.unit_size,
            unit=model.unit,
            package_quantity=model.package_quantity,
            offer_price=model.offer_price,
            currency=model.currency,
            tax_included=model.tax_included,
            page_number=model.page_number,
            confidence_score=model.confidence_score,
            metadata=model.metadata_,
            created_at=model.created_at,
        )

    @staticmethod
    def _to_product_match_candidate_record(
        model: ProductMatchCandidateModel,
    ) -> ProductMatchCandidateRecord:
        return ProductMatchCandidateRecord(
            id=model.id,
            business_id=model.business_id,
            supplier_offer_document_id=model.supplier_offer_document_id,
            supplier_offer_item_id=model.supplier_offer_item_id,
            product_id=model.product_id,
            relationship_type=model.relationship_type,
            confidence_score=model.confidence_score,
            reasons=[str(reason) for reason in model.reasons],
            cost_difference=model.cost_difference,
            cost_difference_percentage=model.cost_difference_percentage,
            estimated_margin_percentage=model.estimated_margin_percentage,
            recommendation=model.recommendation,
            status=ProductMatchCandidateStatus(model.status),
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    @staticmethod
    def _to_product_match_feedback(model: ProductMatchFeedbackModel) -> ProductMatchFeedback:
        return ProductMatchFeedback(
            id=model.id,
            business_id=model.business_id,
            product_match_candidate_id=model.product_match_candidate_id,
            supplier_offer_item_id=model.supplier_offer_item_id,
            candidate_product_id=model.candidate_product_id,
            relationship_type=model.relationship_type,
            accepted=model.accepted,
            reviewed_by_user_id=model.reviewed_by_user_id,
            notes=model.notes,
            created_at=model.created_at,
        )

    @staticmethod
    def _to_supplier_product(model: SupplierProductModel) -> SupplierProduct:
        return SupplierProduct(
            id=model.id,
            business_id=model.business_id,
            supplier_id=model.supplier_id,
            product_id=model.product_id,
            supplier_product_name=model.supplier_product_name,
            supplier_product_normalized_name=model.supplier_product_normalized_name,
            cost_price=model.cost_price,
            currency=model.currency,
            tax_included=model.tax_included,
            package_quantity=model.package_quantity,
            observed_at=model.observed_at,
            metadata=model.metadata_,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
