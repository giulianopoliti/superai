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

    def recent_messages(self, request: AssistantRequest) -> list[ConversationMessage]: ...


def with_conversation_id(request: AssistantRequest, payload: dict) -> dict:
    if request.conversation_id:
        return {**payload, "conversation_id": str(request.conversation_id)}
    return payload


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
            payload=with_conversation_id(request, {"raw_payload": request.raw_payload}),
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
            payload=with_conversation_id(request, response.model_dump(mode="json")),
        )
        self._messages.append(message)
        return message

    def list_by_business(self, business_id: str) -> list[ConversationMessage]:
        return [message for message in self._messages if message.business_id == business_id]

    def recent_messages(self, request: AssistantRequest) -> list[ConversationMessage]:
        session_id = str(request.conversation_id) if request.conversation_id else None
        messages = [
            message for message in self._messages
            if message.business_id == request.business_id
            and message.external_user_id == request.external_user_id
            and message.channel == request.channel
            and message.payload.get("conversation_id") == session_id
        ]
        return messages[-16:]
