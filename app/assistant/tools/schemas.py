from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.sales.schemas import ProductSalesSummary


class AssistantToolName(StrEnum):
    CATALOG_RESOLVE_FAMILIES = "catalog.resolve_families"
    CATALOG_RESOLVE_PRODUCTS = "catalog.resolve_products"
    SALES_TOP_PRODUCTS = "sales.top_products"
    SALES_SUMMARY = "sales.summary"
    SALES_PRODUCT_HISTORY = "sales.product_history"
    SALES_COMPARE_PERIODS = "sales.compare_periods"
    CATALOG_SEARCH_PRODUCTS = "catalog.search_products"
    INVENTORY_STOCK_STATUS = "inventory.stock_status"


class ToolExecutionContext(BaseModel):
    business_id: str
    external_user_id: str | None = None
    channel: str | None = None
    timestamp: datetime | None = None


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
    days: int = Field(default=7, ge=1, le=365)
    period: str | None = Field(default=None, pattern="^(recent|all_time)$")
    sort: str = Field(default="quantity", pattern="^(quantity|revenue|margin)$")
    limit: int = Field(default=10, ge=1, le=50)
    rank_from: int | None = Field(default=None, ge=1, le=50)
    rank_to: int | None = Field(default=None, ge=1, le=50)
    exclude_product_names: list[str] = Field(default_factory=list)
    exclude_product_ids: list[str] = Field(default_factory=list)
    exclude_skus: list[str] = Field(default_factory=list)
    category: str | None = None
    family: str | None = None
    families: list[str] = Field(default_factory=list)
    exclude_families: list[str] = Field(default_factory=list)
    min_quantity: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_date_range(self) -> "TopProductsQuery":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must be before or equal to date_to.")
        if self.rank_from and self.rank_to and self.rank_from > self.rank_to:
            raise ValueError("rank_from must be before or equal to rank_to.")
        return self


class TopProductsToolResponse(BaseModel):
    business_id: str
    date_from: date
    date_to: date
    sort: str
    limit: int
    period: str | None = None
    rank_from: int | None = None
    rank_to: int | None = None
    exclude_product_names: list[str] = Field(default_factory=list)
    exclude_product_ids: list[str] = Field(default_factory=list)
    exclude_skus: list[str] = Field(default_factory=list)
    category: str | None = None
    family: str | None = None
    families: list[str] = Field(default_factory=list)
    resolved_category: str | None = None
    resolved_categories: list[str] = Field(default_factory=list)
    excluded_families: list[str] = Field(default_factory=list)
    resolved_excluded_product_ids: list[str] = Field(default_factory=list)
    unresolved_exclusions: list[str] = Field(default_factory=list)
    min_quantity: Decimal | None = None
    products: list[ProductSalesSummary] = Field(default_factory=list)
    reply: str


class SalesSummaryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    business_id: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    days: int = Field(default=7, ge=1, le=365)
    period: str | None = Field(default=None, pattern="^(recent|all_time)$")
    families: list[str] = Field(default_factory=list)
    family: str | None = None
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
