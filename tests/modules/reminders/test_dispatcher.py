from datetime import UTC, datetime, timedelta

from app.db.repositories.reminders import InMemoryReminderRepository
from app.modules.reminders.dispatcher import ReminderDispatcher
from app.modules.reminders.schemas import ReminderStatus
from app.modules.reminders.service import ReminderService
from app.providers.notifications.in_memory import InMemoryNotificationProvider


def test_dispatch_due_sends_notification_and_marks_notified() -> None:
    now = datetime(2026, 7, 22, 13, 0, tzinfo=UTC)
    repository = InMemoryReminderRepository()
    service = ReminderService(repository)
    notification_provider = InMemoryNotificationProvider()
    dispatcher = ReminderDispatcher(
        reminder_service=service,
        notification_provider=notification_provider,
    )
    reminder = service.create_reminder(
        business_id="business-1",
        created_by_external_user_id="541169405063",
        title="Comprar bolsas",
        due_at=now - timedelta(minutes=1),
    )

    results = dispatcher.dispatch_due(now=now)

    assert len(results) == 1
    assert results[0].notification_status == "sent"
    assert notification_provider.sent[0].message == "Recordatorio: Comprar bolsas"
    assert repository.get_pending_by_id("business-1", reminder.id) is None
    assert repository._reminders[reminder.id].status == ReminderStatus.NOTIFIED
