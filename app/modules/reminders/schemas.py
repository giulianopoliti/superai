from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class ReminderStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    CANCELLED = "cancelled"


class Reminder(BaseModel):
    id: str
    business_id: str
    created_by_external_user_id: str
    title: str = Field(min_length=1)
    description: str | None = None
    due_at: datetime | None = None
    recurrence_rule: str | None = None
    status: ReminderStatus = ReminderStatus.PENDING
    completed_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
