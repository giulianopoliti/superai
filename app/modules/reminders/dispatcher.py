from dataclasses import dataclass
from datetime import UTC, datetime

from app.modules.reminders.service import ReminderService
from app.providers.notifications.base import NotificationProvider


@dataclass(frozen=True)
class ReminderDispatchResult:
    reminder_id: str
    status: str
    notification_status: str
    metadata: dict[str, object]


class ReminderDispatcher:
    def __init__(
        self,
        *,
        reminder_service: ReminderService,
        notification_provider: NotificationProvider,
        default_channel: str = "whatsapp",
    ) -> None:
        self._reminder_service = reminder_service
        self._notification_provider = notification_provider
        self._default_channel = default_channel

    def dispatch_due(self, *, now: datetime | None = None) -> list[ReminderDispatchResult]:
        dispatch_time = now or datetime.now(UTC)
        results: list[ReminderDispatchResult] = []

        for reminder in self._reminder_service.list_due_reminders(dispatch_time):
            notification = self._notification_provider.send_text(
                channel=self._default_channel,
                external_user_id=reminder.created_by_external_user_id,
                message=f"Recordatorio: {reminder.title}",
            )
            if notification.status == "sent":
                self._reminder_service.mark_notified(reminder, now=dispatch_time)

            results.append(
                ReminderDispatchResult(
                    reminder_id=reminder.id,
                    status="processed",
                    notification_status=notification.status,
                    metadata=notification.metadata,
                )
            )

        return results
