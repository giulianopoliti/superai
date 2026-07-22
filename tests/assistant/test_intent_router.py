from datetime import datetime
from zoneinfo import ZoneInfo

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
        timestamp=datetime(2026, 7, 22, 9, 0, tzinfo=ZoneInfo("America/Buenos_Aires")),
    )


def test_routes_create_reminder() -> None:
    result = IntentRouter().route(make_request("recordame revisar la camara"))

    assert result.intent == IntentName.CREATE_REMINDER
    assert result.entities["title"] == "revisar la camara"


def test_routes_create_reminder_with_due_at() -> None:
    result = IntentRouter().route(make_request("recordame cortar fiambre a las 10:05hs de hoy"))

    assert result.intent == IntentName.CREATE_REMINDER
    assert result.entities["title"] == "cortar fiambre"
    assert result.entities["due_at"] == "2026-07-22T10:05:00-03:00"


def test_routes_list_reminders() -> None:
    result = IntentRouter().route(make_request("mostrar recordatorios pendientes"))

    assert result.intent == IntentName.LIST_REMINDERS


def test_routes_unknown() -> None:
    result = IntentRouter().route(make_request("buen dia"))

    assert result.intent == IntentName.UNKNOWN
