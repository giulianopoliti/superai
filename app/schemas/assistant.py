from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class Channel(StrEnum):
    WHATSAPP = "whatsapp"
    WEB = "web"
    DESKTOP = "desktop"
    CLI = "cli"


class MessageType(StrEnum):
    TEXT = "text"
    AUDIO = "audio"
    IMAGE = "image"
    PDF = "pdf"


class Attachment(BaseModel):
    type: MessageType
    url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AssistantAction(BaseModel):
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class AssistantRequest(BaseModel):
    channel: Channel
    external_user_id: str = Field(min_length=1)
    business_id: str = Field(min_length=1)
    message_type: MessageType
    text: str | None = None
    attachments: list[Attachment] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    raw_payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_content(self) -> "AssistantRequest":
        if not self.text and not self.attachments:
            raise ValueError("AssistantRequest requires text or at least one attachment.")
        return self


class AssistantResponse(BaseModel):
    reply: str
    actions: list[AssistantAction] = Field(default_factory=list)
    requires_confirmation: bool = False
    confirmation_payload: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
