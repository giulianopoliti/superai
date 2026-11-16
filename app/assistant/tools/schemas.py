from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.catalog.product_view import CatalogAttribute
from app.modules.sales.schemas import ProductSalesSummary


class AssistantToolName(StrEnum):
    CATALOG_RESOLVE_FAMILIES = "catalog.resolve_families"
    CATALOG_RESOLVE_PRODUCTS = "catalog.resolve_products"
    SALES_TOP_PRODUCTS = "sales.top_products"
    SALES_SUMMARY = "sales.summary"
    SALES_PRODUCT_HISTORY = "sales.product_history"
    SALES_COMPARE_PERIODS = "sales.compare_periods"
    SALES_BASKET_ASSOCIATIONS = "sales.basket_associations"
    CATALOG_SEARCH_PRODUCTS = "catalog.search_products"
    INVENTORY_STOCK_STATUS = "inventory.stock_status"


class ToolExecutionContext(BaseModel):
    business_id: str
    external_user_id: str | None = None
    channel: str | None = None
    timestamp: datetime | None = None
    text: str | None = None


class AssistantToolCall(BaseModel):
    name: AssistantToolName
    arguments: dict[str, Any] = Field(default_factory=dict)


class AssistantToolResult(BaseModel):
    name: AssistantToolName
    reply: str
    data: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AssistantToolDefinition(BaseModel):
    name: AssistantToolName
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    read_only: bool = True
    requires_confirmation: bool = False


class ResolveFamiliesQuery(BaseModel):
    terms: list[str] = Field(default_factory=list)


class ResolvedFamily(BaseModel):
    requested: str
    family: str | None = None
    confidence: float = 0
    candidates: list[str] = Field(default_factory=list)


class ResolveFamiliesResponse(BaseModel):
    families: list[ResolvedFamily] = Field(default_factory=list)
    reply: str


class ResolveProductsQuery(BaseModel):
    terms: list[str] = Field(default_factory=list)
    limit: int = Field(default=5, ge=1, le=20)


class ResolvedProduct(BaseModel):
    requested: str
    product_id: str | None = None
    product_name: str | None = None
    sku: str | None = None
    confidence: float = 0
    candidates: list[dict[str, str | None]] = Field(default_factory=list)


class ResolveProductsResponse(BaseModel):
    products: list[ResolvedProduct] = Field(default_factory=list)
    reply: str


class TopProductsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_id: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    compare: bool = False
    growth: bool = False
    baseline_weeks: int = Field(default=1, ge=1, le=12)
    show_stock: bool = True
    comparison_date_from: date | None = None
    comparison_date_to: date | None = None
    days: int = Field(default=7, ge=1, le=365)
    period: str | None = Field(default=None, pattern="^(recent|all_time)$")
    sort: str = Field(default="quantity", pattern="^(quantity|revenue|margin)$")
    limit: int = Field(default=10, ge=1, le=100)
    rank_from: int | None = Field(default=None, ge=1, le=2147483647)
    rank_to: int | None = Field(default=None, ge=1, le=2147483647)
    exclude_product_names: list[str] = Field(default_factory=list)
    exclude_product_ids: list[str] = Field(default_factory=list)
    include_irrelevant_products: bool = Field(
        default=False,
        description="Incluye productos marcados como poco relevantes solo en este ranking.",
    )
    exclude_skus: list[str] = Field(default_factory=list)
    category: str | None = None
    family: str | None = None
    supplier: str | None = Field(default=None, min_length=1, max_length=255)
    supplier_optional: bool = Field(
        default=False,
        description="Permite usar solo familia si no hay vínculos con el proveedor solicitado.",
    )
    auxiliary_filters: dict[CatalogAttribute, str] = Field(
        default_factory=dict,
        description="Filtros exactos por clasificación auxiliar actual; distintos de family.",
    )
    families: list[str] = Field(default_factory=list)
    exclude_families: list[str] = Field(default_factory=list)
    min_quantity: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_date_range(self) -> "TopProductsQuery":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must be before or equal to date_to.")
        if self.rank_from and self.rank_to and self.rank_from > self.rank_to:
            raise ValueError("rank_from must be before or equal to rank_to.")
        if bool(self.comparison_date_from) != bool(self.comparison_date_to) or (
            self.comparison_date_from and self.comparison_date_to
            and self.comparison_date_from > self.comparison_date_to
        ):
            raise ValueError("Invalid comparison range")
        return self


class RankingComparisonRow(BaseModel):
    product_id: str | None
    external_product_id: str | None
    product_name: str | None
    current: Decimal | None
    previous: Decimal | None
    absolute_change: Decimal | None
    percent_change: Decimal | None
    current_stock: Decimal | None
    unit: str | None
    baseline_equivalent: Decimal | None = None


class RankingComparison(BaseModel):
    date_from: date
    date_to: date
    metric: str
    data_available: bool
    rows: list[RankingComparisonRow]
    baseline_periods: list[tuple[date, date]] = Field(default_factory=list)


class TopProductsToolResponse(BaseModel):
    business_id: str
    date_from: date
    date_to: date
    sort: str
    growth: bool = False
    limit: int
    period: str | None = None
    rank_from: int | None = None
    rank_to: int | None = None
    exclude_product_names: list[str] = Field(default_factory=list)
    exclude_product_ids: list[str] = Field(default_factory=list)
    exclude_skus: list[str] = Field(default_factory=list)
    category: str | None = None
    family: str | None = None
    supplier: str | None = None
    supplier_filter_applied: bool = False
    supplier_fallback_reason: str | None = None
    auxiliary_filters: dict[CatalogAttribute, str] = Field(default_factory=dict)
    families: list[str] = Field(default_factory=list)
    resolved_category: str | None = None
    resolved_categories: list[str] = Field(default_factory=list)
    excluded_families: list[str] = Field(default_factory=list)
    include_irrelevant_products: bool = False
    preference_excluded_product_ids: list[str] = Field(default_factory=list)
    preference_excluded_product_count: int = 0
    resolved_excluded_product_ids: list[str] = Field(default_factory=list)
    unresolved_exclusions: list[str] = Field(default_factory=list)
    min_quantity: Decimal | None = None
    products: list[ProductSalesSummary] = Field(default_factory=list)
    comparison: RankingComparison | None = None
    reply: str


class BasketAssociationsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_from: date | None = None
    date_to: date | None = None
    days: int = Field(default=30, ge=1, le=365)
    limit: int = Field(default=5, ge=1, le=20)
    min_tickets: int = Field(default=3, ge=2)
    level: str = Field(default="product", pattern="^(product|family)$")
    per_family: bool = False
    family_a: str | None = None
    family_b: str | None = None
    include_families: list[str] = Field(default_factory=list, max_length=20)
    exclude_families: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_range(self) -> "BasketAssociationsQuery":
        if self.date_from and self.date_to and not 0 <= (self.date_to - self.date_from).days < 365:
            raise ValueError("Invalid basket date range")
        if self.family_b and not self.family_a:
            raise ValueError("family_a is required with family_b")
        return self


class SalesSummaryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    business_id: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    days: int = Field(default=7, ge=1, le=365)
    period: str | None = Field(default=None, pattern="^(recent|all_time)$")
    families: list[str] = Field(default_factory=list)
    family: str | None = None
    auxiliary_filters: dict[CatalogAttribute, str] = Field(
        default_factory=dict,
        description="Filtros exactos por clasificación auxiliar actual; distintos de family.",
    )
    category: str | None = None
    exclude_families: list[str] = Field(default_factory=list)
    exclude_product_names: list[str] = Field(default_factory=list)
    exclude_product_ids: list[str] = Field(default_factory=list)
    exclude_skus: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_dates(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must be before or equal to date_to")
        return self


class ProductHistoryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product: str = Field(min_length=1)
    date_from: date | None = None
    date_to: date | None = None
    days: int = Field(default=7, ge=1, le=365)

    @model_validator(mode="after")
    def validate_dates(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must be before or equal to date_to")
        return self


class ComparePeriodsQuery(SalesSummaryQuery):
    comparison_date_from: date | None = None
    comparison_date_to: date | None = None

    @model_validator(mode="after")
    def validate_comparison(self):
        if bool(self.comparison_date_from) != bool(self.comparison_date_to):
            raise ValueError("Both comparison dates are required")
        if (
            self.comparison_date_from
            and self.comparison_date_to
            and self.comparison_date_from > self.comparison_date_to
        ):
            raise ValueError("Invalid comparison range")
        return self


class SearchProductsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = ""
    family: str | None = None
    limit: int = Field(default=20, ge=1, le=50)


class StockStatusQuery(SearchProductsQuery):
    max_stock: Decimal | None = None
