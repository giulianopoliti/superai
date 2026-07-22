from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import UTC, datetime
from typing import Any
from urllib import error, request

from app.schemas.assistant import (
    AssistantRequest,
    AssistantResponse,
    Attachment,
    Channel,
    MessageType,
)


class KapsoWebhookError(ValueError):
    """Raised when a Kapso webhook payload cannot be normalized."""


def verify_kapso_signature(raw_body: bytes, signature: str | None, secret: str | None) -> bool:
    if not secret:
        return True
    if not signature:
        return False

    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


class KapsoWhatsAppAdapter:
    def __init__(
        self,
        *,
        business_id: str,
        api_key: str | None = None,
        phone_number_id: str | None = None,
    ) -> None:
        self._business_id = business_id
        self._api_key = api_key
        self._phone_number_id = phone_number_id

    def to_assistant_requests(self, payload: dict[str, Any]) -> list[AssistantRequest]:
        events = self._extract_events(payload)
        return [self._event_to_request(event, payload) for event in events]

    def send_response(
        self,
        assistant_request: AssistantRequest,
        response: AssistantResponse,
    ) -> dict[str, object]:
        api_key = self._api_key or os.getenv("KAPSO_API_KEY")
        if not api_key:
            return {"status": "skipped", "reason": "missing_api_key"}

        recipient = self._resolve_recipient(assistant_request.raw_payload)
        phone_number_id = self._resolve_phone_number_id(assistant_request.raw_payload)
        if not recipient or not phone_number_id:
            return {
                "status": "skipped",
                "reason": "missing_recipient_or_phone_number_id",
                "has_recipient": bool(recipient),
                "has_phone_number_id": bool(phone_number_id),
            }

        message_payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "type": "text",
            "text": {"preview_url": False, "body": response.reply},
        }
        if recipient.startswith(("AR.", "US.")):
            message_payload["recipient"] = recipient
        else:
            message_payload["to"] = recipient

        url = f"https://api.kapso.ai/meta/whatsapp/v24.0/{phone_number_id}/messages"
        body = json.dumps(message_payload).encode("utf-8")
        outbound_request = request.Request(
            url,
            data=body,
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
                return {"status": "sent", "phone_number_id": phone_number_id}
        except error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            return {
                "status": "failed",
                "reason": "http_error",
                "status_code": exc.code,
                "body": error_body[:500],
            }
        except error.URLError as exc:
            return {
                "status": "failed",
                "reason": "url_error",
                "error": exc.reason.__class__.__name__,
            }

    @staticmethod
    def _extract_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
        if payload.get("batch") is True:
            data = payload.get("data")
            if not isinstance(data, list):
                raise KapsoWebhookError("Batched Kapso payload requires a data list.")
            return [event for event in data if isinstance(event, dict)]
        return [payload]

    def _event_to_request(
        self,
        event: dict[str, Any],
        raw_payload: dict[str, Any],
    ) -> AssistantRequest:
        message = event.get("message") if isinstance(event.get("message"), dict) else event
        if not isinstance(message, dict):
            raise KapsoWebhookError("Kapso event requires a message object.")

        message_type = self._message_type(message)
        text = self._message_text(message)
        attachments = self._attachments(message, message_type)

        return AssistantRequest(
            channel=Channel.WHATSAPP,
            external_user_id=self._external_user_id(event, message),
            business_id=self._business_id,
            message_type=message_type,
            text=text,
            attachments=attachments,
            timestamp=self._timestamp(message),
            raw_payload={
                "event": event,
                "webhook_payload": raw_payload,
            },
        )

    @staticmethod
    def _external_user_id(event: dict[str, Any], message: dict[str, Any]) -> str:
        kapso = message.get("kapso") if isinstance(message.get("kapso"), dict) else {}
        conversation = (
            event.get("conversation") if isinstance(event.get("conversation"), dict) else {}
        )
        candidates = (
            message.get("from_user_id"),
            message.get("from_parent_user_id"),
            message.get("username"),
            conversation.get("business_scoped_user_id"),
            conversation.get("parent_business_scoped_user_id"),
            conversation.get("username"),
            message.get("from"),
            conversation.get("phone_number"),
            kapso.get("phone_number"),
        )
        for candidate in candidates:
            if candidate:
                return str(candidate)
        raise KapsoWebhookError("Kapso message does not include a usable sender identity.")

    @staticmethod
    def _message_type(message: dict[str, Any]) -> MessageType:
        raw_type = str(message.get("type") or "").lower()
        if raw_type == "text" or "text" in message:
            return MessageType.TEXT
        if raw_type == "audio":
            return MessageType.AUDIO
        if raw_type == "image":
            return MessageType.IMAGE
        if raw_type in {"document", "pdf"}:
            return MessageType.PDF
        return MessageType.TEXT

    @staticmethod
    def _message_text(message: dict[str, Any]) -> str | None:
        text = message.get("text")
        if isinstance(text, dict) and text.get("body"):
            return str(text["body"])

        button = message.get("button")
        if isinstance(button, dict) and button.get("text"):
            return str(button["text"])

        kapso = message.get("kapso")
        if isinstance(kapso, dict) and kapso.get("content"):
            return str(kapso["content"])

        return None

    @staticmethod
    def _attachments(message: dict[str, Any], message_type: MessageType) -> list[Attachment]:
        if message_type == MessageType.TEXT:
            return []

        media = message.get(message_type.value)
        metadata = media if isinstance(media, dict) else {}
        url = metadata.get("link") or metadata.get("url") if isinstance(metadata, dict) else None
        return [Attachment(type=message_type, url=url, metadata=dict(metadata))]

    @staticmethod
    def _timestamp(message: dict[str, Any]) -> datetime:
        value = message.get("timestamp")
        if value is None:
            return datetime.now(UTC)
        try:
            return datetime.fromtimestamp(int(value), tz=UTC)
        except (TypeError, ValueError, OSError):
            return datetime.now(UTC)

    def _resolve_phone_number_id(self, raw_payload: dict[str, Any]) -> str | None:
        event = raw_payload.get("event")
        if isinstance(event, dict):
            message = event.get("message") if isinstance(event.get("message"), dict) else event
            if isinstance(message, dict):
                kapso = message.get("kapso") if isinstance(message.get("kapso"), dict) else {}
                if kapso.get("phone_number_id"):
                    return str(kapso["phone_number_id"])
            if event.get("phone_number_id"):
                return str(event["phone_number_id"])
            conversation = (
                event.get("conversation") if isinstance(event.get("conversation"), dict) else {}
            )
            if conversation.get("phone_number_id"):
                return str(conversation["phone_number_id"])
        return self._phone_number_id

    @staticmethod
    def _resolve_recipient(raw_payload: dict[str, Any]) -> str | None:
        event = raw_payload.get("event")
        message = event.get("message") if isinstance(event, dict) else None
        if not isinstance(message, dict) and isinstance(event, dict):
            message = event
        if not isinstance(message, dict):
            return None

        kapso = message.get("kapso") if isinstance(message.get("kapso"), dict) else {}
        conversation = (
            event.get("conversation") if isinstance(event.get("conversation"), dict) else {}
        )
        candidates = (
            message.get("from"),
            conversation.get("phone_number"),
            message.get("from_user_id"),
            message.get("from_parent_user_id"),
            message.get("username"),
            conversation.get("business_scoped_user_id"),
            conversation.get("parent_business_scoped_user_id"),
            conversation.get("username"),
            kapso.get("phone_number"),
        )
        for candidate in candidates:
            if candidate:
                return str(candidate)
        return None
