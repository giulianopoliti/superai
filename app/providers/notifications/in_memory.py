from dataclasses import dataclass, field

from app.providers.notifications.base import NotificationProvider, NotificationResult


@dataclass
class SentNotification:
    channel: str
    external_user_id: str
    message: str


class InMemoryNotificationProvider(NotificationProvider):
    def __init__(self) -> None:
        self.sent: list[SentNotification] = []

    def send_text(
        self,
        *,
        channel: str,
        external_user_id: str,
        message: str,
    ) -> NotificationResult:
        self.sent.append(
            SentNotification(
                channel=channel,
                external_user_id=external_user_id,
                message=message,
            )
        )
        return NotificationResult(status="sent", metadata={})


@dataclass
class NotificationCollector:
    provider: InMemoryNotificationProvider = field(default_factory=InMemoryNotificationProvider)
