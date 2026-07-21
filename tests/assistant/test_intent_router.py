from datetime import UTC, datetime

from app.assistant.intent_router import IntentRouter
from app.schemas.assistant import AssistantRequest, Channel, MessageType
from app.schemas.intents import IntentName


def make_request(text: str) -> AssistantRequest:
    return AssistantRequest(
        channel=Channel.CLI,
        external_user_id="user-1",
        business_id="business-1",
        message_type=MessageType.TEXT,
        text=text,
        timestamp=datetime.now(UTC),
    )


def test_routes_create_reminder() -> None:
    result = IntentRouter().route(make_request("recordame revisar la cámara"))

    assert result.intent == IntentName.CREATE_REMINDER
    assert result.entities["title"] == "revisar la cámara"


def test_routes_list_reminders() -> None:
    result = IntentRouter().route(make_request("mostrar recordatorios pendientes"))

    assert result.intent == IntentName.LIST_REMINDERS


def test_routes_unknown() -> None:
    result = IntentRouter().route(make_request("buen día"))

    assert result.intent == IntentName.UNKNOWN
