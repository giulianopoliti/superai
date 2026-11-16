from datetime import UTC, date, datetime, timedelta

from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.db.repositories.sql_reminders import SqlReminderRepository
from app.db.repositories.sql_sales import SqlSalesRepository
from app.modules.operations.schemas import (
    CatalogHealthSummary,
    DailySummaryResponse,
    OperationalAlert,
    OperationalAlertSeverity,
    OperationsDashboardResponse,
    OperationsHomeResponse,
    SalesTotalsSummary,
)
from app.modules.sales.purchase_suggestions import PurchaseSuggestionService
from app.modules.sales.schemas import ProductSalesSummary


class OperationsDashboardService:
    def __init__(
        self,
        *,
        sales_repository: SqlSalesRepository,
        procurement_repository: SqlProcurementRepository,
        reminder_repository: SqlReminderRepository,
    ) -> None:
        self._sales_repository = sales_repository
        self._procurement_repository = procurement_repository
        self._reminder_repository = reminder_repository

    def build_dashboard(
        self,
        *,
        business_id: str,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 10,
        target_coverage_days: int = 7,
        safety_days: int = 0,
        use_stock: bool = True,
    ) -> OperationsDashboardResponse:
        now = datetime.now(UTC)
        sales_status = self._sales_repository.get_sales_history_status(business_id=business_id)
        resolved_date_to = date_to or sales_status.last_sale_date or now.date()
        resolved_date_from = date_from or resolved_date_to - timedelta(days=6)
        limit = min(max(limit, 1), 50)

        top_by_quantity = self._sales_repository.top_products(
            business_id=business_id,
            date_from=resolved_date_from,
            date_to=resolved_date_to,
            sort="quantity",
            limit=limit,
        )
        top_by_revenue = self._sales_repository.top_products(
            business_id=business_id,
            date_from=resolved_date_from,
            date_to=resolved_date_to,
            sort="revenue",
            limit=limit,
        )
        top_by_margin = self._sales_repository.top_products(
            business_id=business_id,
            date_from=resolved_date_from,
            date_to=resolved_date_to,
            sort="margin",
            limit=limit,
        )
        purchase_suggestions = PurchaseSuggestionService().build_suggestions(
            sales_summaries=top_by_quantity,
            target_coverage_days=target_coverage_days,
            safety_days=safety_days,
            use_stock=use_stock,
            ignore_negative_stock=True,
        )
        pending_reminders = self._reminder_repository.list_pending(business_id)
        overdue_reminders = [
            reminder
            for reminder in pending_reminders
            if reminder.due_at is not None and self._as_aware_datetime(reminder.due_at) <= now
        ]
        catalog_health = CatalogHealthSummary(
            **self._procurement_repository.summarize_products(business_id=business_id)
        )
        supplier_offers = self._procurement_repository.list_supplier_offer_document_summaries(
            business_id=business_id,
            limit=limit,
        )
        sales_totals = SalesTotalsSummary(
            **self._sales_repository.sales_totals(
                business_id=business_id,
                date_from=resolved_date_from,
                date_to=resolved_date_to,
            )
        )
        alerts = self._build_alerts(
            sales_status=sales_status,
            top_products=top_by_quantity,
            pending_reminder_count=len(pending_reminders),
            overdue_reminder_count=len(overdue_reminders),
            catalog_health=catalog_health,
            supplier_offer_count=len(supplier_offers),
        )
        return OperationsDashboardResponse(
            business_id=business_id,
            generated_at=now,
            date_from=resolved_date_from,
            date_to=resolved_date_to,
            sales_status=sales_status,
            sales_totals=sales_totals,
            top_products_by_quantity=top_by_quantity,
            top_products_by_revenue=top_by_revenue,
            top_products_by_margin=top_by_margin,
            purchase_suggestions=purchase_suggestions,
            pending_reminders=pending_reminders[:limit],
            overdue_reminders=overdue_reminders[:limit],
            supplier_offers=supplier_offers,
            catalog_health=catalog_health,
            alerts=alerts,
        )

    def build_home(
        self, *, business_id: str, date_from: date, date_to: date,
    ) -> OperationsHomeResponse:
        """Read only the data rendered by the home; never load document analysis."""
        if date_from > date_to:
            raise ValueError("El inicio del período debe ser anterior al final.")
        now = datetime.now(UTC)
        status = self._sales_repository.get_sales_history_status(business_id=business_id)
        totals = self._sales_repository.query_sales_summary(
            business_id=business_id, date_from=date_from, date_to=date_to,
        )
        top = self._sales_repository.top_products(
            business_id=business_id, date_from=date_from, date_to=date_to,
            sort="quantity", limit=5,
            apply_saved_preferences=True,
        )
        pending, overdue = self._reminder_repository.count_pending(business_id=business_id, now=now)
        counts = self._procurement_repository.home_counts(business_id=business_id)
        recent = self._procurement_repository.recent_supplier_offers(business_id=business_id)
        return OperationsHomeResponse(
            business_id=business_id, generated_at=now, date_from=date_from, date_to=date_to,
            sales_status=status,
            sales_totals=SalesTotalsSummary(**{
                key: totals[key] for key in SalesTotalsSummary.model_fields
            }),
            top_products_by_quantity=top,
            pending_reminder_count=pending, overdue_reminder_count=overdue,
            supplier_offer_count=counts["supplier_offer_count"], recent_supplier_offers=recent,
            alerts=self._build_alerts(
                sales_status=status, top_products=top,
                pending_reminder_count=pending, overdue_reminder_count=overdue,
                catalog_health=CatalogHealthSummary(
                    total_count=counts["total_count"],
                    missing_cost_count=counts["missing_cost_count"],
                ), supplier_offer_count=counts["supplier_offer_count"],
            ),
        )

    def build_daily_summary(
        self,
        *,
        business_id: str,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> DailySummaryResponse:
        dashboard = self.build_dashboard(
            business_id=business_id,
            date_from=date_from,
            date_to=date_to,
            limit=5,
        )
        lines = [
            f"Resumen operativo del {dashboard.date_from:%d/%m} al {dashboard.date_to:%d/%m}.",
            (
                f"Ventas: {dashboard.sales_totals.quantity_sold} unidades, "
                f"${dashboard.sales_totals.revenue} de facturacion y "
                f"${dashboard.sales_totals.gross_margin} de margen bruto."
            ),
            (
                f"Recordatorios: {len(dashboard.pending_reminders)} pendientes, "
                f"{len(dashboard.overdue_reminders)} vencidos."
            ),
            (
                f"Alertas: {len(dashboard.alerts)} activas. "
                f"Ofertas de proveedores recientes: {len(dashboard.supplier_offers)}."
            ),
        ]
        if dashboard.top_products_by_quantity:
            top_product = dashboard.top_products_by_quantity[0]
            product_name = top_product.product_name or top_product.external_product_id or "producto"
            lines.append(f"Mas vendido: {product_name} con {top_product.quantity_sold} unidades.")
        return DailySummaryResponse(
            business_id=business_id,
            generated_at=dashboard.generated_at,
            date_from=dashboard.date_from,
            date_to=dashboard.date_to,
            summary="\n".join(lines),
            dashboard=dashboard,
        )

    @staticmethod
    def _as_aware_datetime(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _build_alerts(
        *,
        sales_status,
        top_products: list[ProductSalesSummary],
        pending_reminder_count: int,
        overdue_reminder_count: int,
        catalog_health: CatalogHealthSummary,
        supplier_offer_count: int,
    ) -> list[OperationalAlert]:
        alerts: list[OperationalAlert] = []
        if sales_status.total_sale_lines == 0:
            alerts.append(
                OperationalAlert(
                    type="sales.no_history",
                    severity=OperationalAlertSeverity.WARNING,
                    title="No hay ventas cargadas",
                    description="Importa ventas del POS para activar consultas y sugerencias.",
                )
            )
        if overdue_reminder_count:
            alerts.append(
                OperationalAlert(
                    type="reminders.overdue",
                    severity=OperationalAlertSeverity.CRITICAL,
                    title="Hay recordatorios vencidos",
                    description=f"{overdue_reminder_count} recordatorios necesitan atencion.",
                    metadata={"count": overdue_reminder_count},
                )
            )
        low_stock_products = [
            product
            for product in top_products
            if product.estimated_stock_days is not None and product.estimated_stock_days <= 3
        ]
        if low_stock_products:
            alerts.append(
                OperationalAlert(
                    type="sales.low_stock_coverage",
                    severity=OperationalAlertSeverity.WARNING,
                    title="Productos vendidos con poca cobertura",
                    description=(
                        f"{len(low_stock_products)} productos top tienen 3 dias o menos "
                        "de stock estimado."
                    ),
                    metadata={"count": len(low_stock_products)},
                )
            )
        if catalog_health.missing_cost_count:
            alerts.append(
                OperationalAlert(
                    type="catalog.missing_costs",
                    severity=OperationalAlertSeverity.WARNING,
                    title="Productos sin costo",
                    description=(
                        f"{catalog_health.missing_cost_count} productos no tienen costo cargado."
                    ),
                    metadata={"count": catalog_health.missing_cost_count},
                )
            )
        if pending_reminder_count:
            alerts.append(
                OperationalAlert(
                    type="reminders.pending",
                    severity=OperationalAlertSeverity.INFO,
                    title="Recordatorios pendientes",
                    description=f"{pending_reminder_count} recordatorios siguen abiertos.",
                    metadata={"count": pending_reminder_count},
                )
            )
        if supplier_offer_count:
            alerts.append(
                OperationalAlert(
                    type="procurement.supplier_offers_ready",
                    severity=OperationalAlertSeverity.INFO,
                    title="Ofertas de proveedores disponibles",
                    description=f"{supplier_offer_count} listas recientes listas para revisar.",
                    metadata={"count": supplier_offer_count},
                )
            )
        return alerts
