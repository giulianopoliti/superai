from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base
from app.db.repositories.sql_conversations import SqlConversationMessageRepository
from app.db.repositories.sql_reminders import SqlReminderRepository
from app.modules.reminders.schemas import ReminderStatus
from app.modules.reminders.service import ReminderService
from app.schemas.assistant import AssistantRequest, AssistantResponse, Channel, MessageType


def build_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_sql_reminder_repository_scopes_by_business() -> None:
    session = build_session()
    service = ReminderService(SqlReminderRepository(session))
    service.create_reminder(
        business_id="business-1",
        created_by_external_user_id="user-1",
        title="Revisar heladera",
    )

    assert service.list_pending_reminders("business-2") == []
    assert len(service.list_pending_reminders("business-1")) == 1


def test_sql_reminder_repository_marks_done() -> None:
    session = build_session()
    service = ReminderService(SqlReminderRepository(session))
    reminder = service.create_reminder(
        business_id="business-1",
        created_by_external_user_id="user-1",
        title="Revisar heladera",
    )

    completed = service.mark_done(business_id="business-1", reminder_id=reminder.id)

    assert completed is not None
    assert completed.status == ReminderStatus.DONE
    assert completed.completed_at is not None


def test_sql_conversation_repository_saves_inbound_and_outbound() -> None:
    session = build_session()
    repository = SqlConversationMessageRepository(session)
    request = AssistantRequest(
        channel=Channel.CLI,
        external_user_id="user-1",
        business_id="business-1",
        message_type=MessageType.TEXT,
        text="hola",
    )

    repository.save_inbound(request)
    repository.save_outbound(request, AssistantResponse(reply="Listo"))

    messages = repository.list_by_business("business-1")
    assert [message.direction for message in messages] == ["inbound", "outbound"]
    assert repository.list_by_business("business-2") == []
