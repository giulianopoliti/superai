from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class NotificationResult:
    status: str
    metadata: dict[str, object]


class NotificationProvider(Protocol):
    def send_text(
        self,
        *,
        channel: str,
        external_user_id: str,
        message: str,
    ) -> NotificationResult: ...
