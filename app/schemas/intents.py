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
    confidence: float = Field(ge=0.0, le=1.0)
    requires_clarification: bool = False
    clarification_question: str | None = None
    raw_time_expression: str | None = None
