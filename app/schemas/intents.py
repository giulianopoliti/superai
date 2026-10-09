from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class IntentName(StrEnum):
    CREATE_REMINDER = "create_reminder"
    LIST_REMINDERS = "list_reminders"
    MARK_REMINDER_DONE = "mark_reminder_done"
    SAVE_EXPIRATION = "save_expiration"
    LIST_EXPIRATIONS = "list_expirations"
    SAVE_SUPPLIER_NOTE = "save_supplier_note"
    GET_SUPPLIER_INFO = "get_supplier_info"
    SAVE_PRODUCT_PRICE = "save_product_price"
    COMPARE_PRODUCT_PRICES = "compare_product_prices"
    SUGGEST_SALE_PRICE = "suggest_sale_price"
    GET_OPERATIONS_DASHBOARD = "get_operations_dashboard"
    LIST_OPERATIONAL_ALERTS = "list_operational_alerts"
    GET_DAILY_SUMMARY = "get_daily_summary"
    QUERY_SALES_TOP_PRODUCTS = "query_sales_top_products"
    QUERY_TOOL = "query_tool"
    UNKNOWN = "unknown"


class IntentResult(BaseModel):
    intent: IntentName
    entities: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)
    requires_clarification: bool = False
    clarification_question: str | None = None


class ReminderIntentExtraction(BaseModel):
    intent: IntentName
    title: str | None = None
    due_at: str | None = None
    timezone: str = "America/Buenos_Aires"
    days: int | None = Field(default=None, ge=1, le=365)
    period: str | None = Field(default=None, pattern="^(recent|all_time)$")
    date_from: str | None = None
    date_to: str | None = None
    sort: str | None = Field(default=None, pattern="^(quantity|revenue|margin)$")
    limit: int | None = Field(default=None, ge=1, le=50)
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
    confidence: float = Field(ge=0.0, le=1.0)
    requires_clarification: bool = False
    clarification_question: str | None = None
    raw_time_expression: str | None = None
