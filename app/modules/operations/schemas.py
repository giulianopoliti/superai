from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field

from app.modules.procurement.schemas import SupplierOfferDocumentSummary
from app.modules.reminders.schemas import Reminder
from app.modules.sales.schemas import (
    ProductSalesSummary,
    PurchaseSuggestion,
    SalesHistoryStatus,
)


class OperationalAlertSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class OperationalAlert(BaseModel):
    type: str
    severity: OperationalAlertSeverity
    title: str
    description: str
    metadata: dict[str, object] = Field(default_factory=dict)


class CatalogHealthSummary(BaseModel):
    total_count: int = 0
    active_count: int = 0
    missing_cost_count: int = 0
    missing_barcode_count: int = 0
    duplicate_barcode_count: int = 0
    categories: list[dict[str, object]] = Field(default_factory=list)


class SalesTotalsSummary(BaseModel):
    quantity_sold: Decimal = Decimal("0")
    revenue: Decimal = Decimal("0")
    cost_amount: Decimal = Decimal("0")
    gross_margin: Decimal = Decimal("0")
    ticket_count: int = 0
    line_count: int = 0


class OperationsDashboardResponse(BaseModel):
    business_id: str
    generated_at: datetime
    date_from: date
    date_to: date
    sales_status: SalesHistoryStatus
    sales_totals: SalesTotalsSummary
    top_products_by_quantity: list[ProductSalesSummary] = Field(default_factory=list)
    top_products_by_revenue: list[ProductSalesSummary] = Field(default_factory=list)
    top_products_by_margin: list[ProductSalesSummary] = Field(default_factory=list)
    purchase_suggestions: list[PurchaseSuggestion] = Field(default_factory=list)
    pending_reminders: list[Reminder] = Field(default_factory=list)
    overdue_reminders: list[Reminder] = Field(default_factory=list)
    supplier_offers: list[SupplierOfferDocumentSummary] = Field(default_factory=list)
    catalog_health: CatalogHealthSummary
    alerts: list[OperationalAlert] = Field(default_factory=list)


class DailySummaryResponse(BaseModel):
    business_id: str
    generated_at: datetime
    date_from: date
    date_to: date
    summary: str
    dashboard: OperationsDashboardResponse
