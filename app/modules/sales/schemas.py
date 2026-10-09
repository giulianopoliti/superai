from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class SalesImportStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class SalesImportResult(BaseModel):
    id: str = Field(default_factory=new_id)
    business_id: str
    source_filename: str
    source_type: str = "pos_sales_items_csv"
    period_start: date
    period_end: date
    status: SalesImportStatus = SalesImportStatus.PENDING
    row_count: int = 0
    imported_count: int = 0
    skipped_count: int = 0
    unique_ticket_count: int = 0
    unique_product_count: int = 0
    errors: list[str] = Field(default_factory=list)
    summary: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


class SalesItemLine(BaseModel):
    id: str = Field(default_factory=new_id)
    business_id: str
    source_import_id: str
    external_sale_item_id: str
    external_sale_id: str
    external_product_id: str
    sold_at: datetime
    sale_date: date
    product_id: str | None = None
    sku: str | None = None
    quantity: Decimal = Decimal("0")
    revenue: Decimal = Decimal("0")
    unit_cost: Decimal = Decimal("0")
    cost_amount: Decimal = Decimal("0")
    gross_margin: Decimal = Decimal("0")
    raw_payload: dict[str, object] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class ProductSalesDaily(BaseModel):
    id: str = Field(default_factory=new_id)
    business_id: str
    source_import_id: str
    external_product_id: str
    sale_date: date
    product_id: str | None = None
    sku: str | None = None
    quantity_sold: Decimal = Decimal("0")
    revenue: Decimal = Decimal("0")
    cost_amount: Decimal = Decimal("0")
    gross_margin: Decimal = Decimal("0")
    ticket_count: int = 0
    line_count: int = 0
    zero_cost_line_count: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ProductSalesSummary(BaseModel):
    business_id: str
    product_id: str | None = None
    external_product_id: str | None = None
    sku: str | None = None
    product_name: str | None = None
    unit: str | None = None
    date_from: date
    date_to: date
    days: int
    quantity_sold: Decimal = Decimal("0")
    revenue: Decimal = Decimal("0")
    cost_amount: Decimal = Decimal("0")
    gross_margin: Decimal = Decimal("0")
    ticket_count: int = 0
    line_count: int = 0
    zero_cost_line_count: int = 0
    current_stock: Decimal | None = None
    current_cost: Decimal | None = None
    sale_price: Decimal | None = None

    @computed_field
    @property
    def average_daily_quantity(self) -> Decimal:
        if self.days <= 0:
            return Decimal("0")
        return (self.quantity_sold / Decimal(self.days)).quantize(Decimal("0.01"))

    @computed_field
    @property
    def estimated_stock_days(self) -> Decimal | None:
        if self.current_stock is None or self.average_daily_quantity <= 0:
            return None
        return (self.current_stock / self.average_daily_quantity).quantize(Decimal("0.01"))


class SalesTopProductsResponse(BaseModel):
    business_id: str
    date_from: date
    date_to: date
    sort: str
    products: list[ProductSalesSummary]


class SalesHistoryStatus(BaseModel):
    business_id: str
    first_sale_date: date | None = None
    last_sale_date: date | None = None
    last_imported_at: datetime | None = None
    last_import_filename: str | None = None
    total_sale_lines: int = 0
    total_daily_records: int = 0


class PurchaseSuggestion(BaseModel):
    sales_summary: ProductSalesSummary
    target_coverage_days: int
    safety_days: int = 0
    use_stock: bool = False
    ignore_negative_stock: bool = True

    @computed_field
    @property
    def demand_quantity(self) -> Decimal:
        coverage_days = Decimal(self.target_coverage_days + self.safety_days)
        return (self.sales_summary.average_daily_quantity * coverage_days).quantize(
            Decimal("0.001")
        )

    @computed_field
    @property
    def stock_deduction(self) -> Decimal:
        if not self.use_stock or self.sales_summary.current_stock is None:
            return Decimal("0.000")
        if self.ignore_negative_stock:
            return max(self.sales_summary.current_stock, Decimal("0")).quantize(Decimal("0.001"))
        return self.sales_summary.current_stock.quantize(Decimal("0.001"))

    @computed_field
    @property
    def suggested_quantity(self) -> Decimal:
        return max(self.demand_quantity - self.stock_deduction, Decimal("0")).quantize(
            Decimal("0.001")
        )


class PurchaseSuggestionsResponse(BaseModel):
    business_id: str
    date_from: date
    date_to: date
    sort: str
    target_coverage_days: int
    safety_days: int
    use_stock: bool
    ignore_negative_stock: bool
    suggestions: list[PurchaseSuggestion]
