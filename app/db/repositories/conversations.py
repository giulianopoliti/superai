from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from app.schemas.assistant import AssistantRequest, AssistantResponse


@dataclass(frozen=True)
class ConversationMessage:
    id: str
    business_id: str
    external_user_id: str
    channel: str
    direction: str
    message_type: str
    text: str | None
    payload: dict[str, object]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class ConversationMessageRepository(Protocol):
    def save_inbound(self, request: AssistantRequest) -> ConversationMessage: ...

    def save_outbound(
        self, request: AssistantRequest, response: AssistantResponse
    ) -> ConversationMessage: ...

    def list_by_business(self, business_id: str) -> list[ConversationMessage]: ...


class InMemoryConversationMessageRepository:
    def __init__(self) -> None:
        self._messages: list[ConversationMessage] = []

    def save_inbound(self, request: AssistantRequest) -> ConversationMessage:
        message = ConversationMessage(
            id=str(uuid4()),
            business_id=request.business_id,
            external_user_id=request.external_user_id,
            channel=request.channel,
            direction="inbound",
            message_type=request.message_type,
            text=request.text,
            payload={"raw_payload": request.raw_payload},
        )
        self._messages.append(message)
        return message

    def save_outbound(
        self, request: AssistantRequest, response: AssistantResponse
    ) -> ConversationMessage:
        message = ConversationMessage(
            id=str(uuid4()),
            business_id=request.business_id,
            external_user_id=request.external_user_id,
            channel=request.channel,
            direction="outbound",
            message_type="text",
            text=response.reply,
            payload=response.model_dump(mode="json"),
        )
        self._messages.append(message)
        return message

    def list_by_business(self, business_id: str) -> list[ConversationMessage]:
        return [message for message in self._messages if message.business_id == business_id]
