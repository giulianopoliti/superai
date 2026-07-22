from datetime import UTC, datetime

from app.assistant.engine import AssistantEngine
from app.assistant.intent_router import IntentRouter
from app.db.repositories.conversations import InMemoryConversationMessageRepository
from app.db.repositories.reminders import InMemoryReminderRepository
from app.modules.reminders.service import ReminderService
from app.schemas.assistant import AssistantRequest, Channel, MessageType


def build_test_engine() -> AssistantEngine:
    reminder_repository = InMemoryReminderRepository()
    conversation_repository = InMemoryConversationMessageRepository()
    return AssistantEngine(
        intent_router=IntentRouter(),
        reminder_service=ReminderService(reminder_repository),
        conversation_repository=conversation_repository,
    )


def make_request(text: str, business_id: str = "business-1") -> AssistantRequest:
    return AssistantRequest(
        channel=Channel.CLI,
        external_user_id="user-1",
        business_id=business_id,
        message_type=MessageType.TEXT,
        text=text,
        timestamp=datetime.now(UTC),
    )


def test_engine_creates_and_lists_reminders() -> None:
    engine = build_test_engine()

    create_response = engine.handle_message(make_request("recordame revisar la heladera"))
    list_response = engine.handle_message(make_request("listar recordatorios pendientes"))

    assert "recordatorio" in create_response.reply
    assert "revisar la heladera" in list_response.reply


def test_engine_scopes_reminders_by_business() -> None:
    engine = build_test_engine()

    engine.handle_message(make_request("recordame revisar caja", business_id="business-1"))
    list_response = engine.handle_message(
        make_request("listar recordatorios pendientes", business_id="business-2")
    )

    assert list_response.reply == "No tenes recordatorios pendientes."


def test_engine_handles_unknown_intent() -> None:
    engine = build_test_engine()

    response = engine.handle_message(make_request("hola"))

    assert "No estoy seguro" in response.reply
    assert not response.actions


def test_engine_formats_due_at_in_argentina_timezone() -> None:
    due_at = datetime(2026, 7, 22, 14, 56, tzinfo=UTC)

    assert AssistantEngine._format_due_text(due_at) == " para 22/07 11:56"
