import json
import os
from urllib import error, request

from app.providers.notifications.base import NotificationProvider, NotificationResult


class KapsoNotificationProvider(NotificationProvider):
    def __init__(
        self,
        *,
        api_key: str | None,
        phone_number_id: str | None,
    ) -> None:
        self._api_key = api_key
        self._phone_number_id = phone_number_id

    def send_text(
        self,
        *,
        channel: str,
        external_user_id: str,
        message: str,
    ) -> NotificationResult:
        if channel != "whatsapp":
            return NotificationResult(status="skipped", metadata={"reason": "unsupported_channel"})

        api_key = self._api_key or os.getenv("KAPSO_API_KEY")
        if not api_key:
            return NotificationResult(status="skipped", metadata={"reason": "missing_api_key"})
        if not self._phone_number_id:
            return NotificationResult(
                status="skipped",
                metadata={"reason": "missing_phone_number_id"},
            )

        payload: dict[str, object] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "type": "text",
            "text": {"preview_url": False, "body": message},
        }
        if external_user_id.startswith(("AR.", "US.")):
            payload["recipient"] = external_user_id
        else:
            payload["to"] = external_user_id

        url = f"https://api.kapso.ai/meta/whatsapp/v24.0/{self._phone_number_id}/messages"
        outbound_request = request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "X-API-Key": api_key,
                "Content-Type": "application/json",
                "User-Agent": "StockAI/0.1 (+https://stock-ai.local)",
            },
        )

        try:
            with request.urlopen(outbound_request, timeout=10) as response_stream:
                response_stream.read()
                return NotificationResult(status="sent", metadata={"channel": channel})
        except error.HTTPError as exc:
            return NotificationResult(
                status="failed",
                metadata={
                    "reason": "http_error",
                    "status_code": exc.code,
                    "body": exc.read().decode("utf-8", errors="replace")[:500],
                },
            )
        except error.URLError as exc:
            return NotificationResult(
                status="failed",
                metadata={"reason": "url_error", "error": exc.reason.__class__.__name__},
            )
