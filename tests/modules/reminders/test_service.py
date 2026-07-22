from datetime import UTC, datetime, timedelta

from app.db.repositories.reminders import InMemoryReminderRepository
from app.modules.reminders.schemas import ReminderStatus
from app.modules.reminders.service import ReminderService


def test_create_and_mark_reminder_done() -> None:
    service = ReminderService(InMemoryReminderRepository())
    reminder = service.create_reminder(
        business_id="business-1",
        created_by_external_user_id="user-1",
        title="Revisar heladera",
    )

    completed = service.mark_done(business_id="business-1", reminder_id=reminder.id)

    assert completed is not None
    assert completed.status == ReminderStatus.DONE
    assert completed.completed_at is not None


def test_list_pending_is_scoped_by_business() -> None:
    service = ReminderService(InMemoryReminderRepository())
    service.create_reminder(
        business_id="business-1",
        created_by_external_user_id="user-1",
        title="Revisar heladera",
    )

    assert service.list_pending_reminders("business-2") == []


def test_list_due_reminders_and_mark_notified() -> None:
    service = ReminderService(InMemoryReminderRepository())
    now = datetime(2026, 7, 22, 13, 0, tzinfo=UTC)
    due = service.create_reminder(
        business_id="business-1",
        created_by_external_user_id="user-1",
        title="Revisar heladera",
        due_at=now - timedelta(minutes=1),
    )
    service.create_reminder(
        business_id="business-1",
        created_by_external_user_id="user-1",
        title="Abrir caja",
        due_at=now + timedelta(minutes=10),
    )

    due_reminders = service.list_due_reminders(now)
    notified = service.mark_notified(due, now=now)

    assert [reminder.id for reminder in due_reminders] == [due.id]
    assert notified.status == ReminderStatus.NOTIFIED
    assert service.list_due_reminders(now) == []
