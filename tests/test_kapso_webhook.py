import hmac
import json
from hashlib import sha256

from app.settings import settings


def kapso_webhook_payload(text: str = "recordame comprar bolsas") -> dict[str, object]:
    return {
        "message": {
            "from": "541169405063",
            "from_user_id": "AR.1787141432462124",
            "id": "wamid.test",
            "kapso": {
                "direction": "inbound",
                "phone_number": "541169405063",
                "phone_number_id": "597907523413541",
            },
            "text": {"body": text},
            "timestamp": "1784651246",
            "type": "text",
        },
        "phone_number_id": "597907523413541",
    }


def test_kapso_webhook_creates_reminder(client) -> None:
    response = client.post(
        "/webhooks/kapso",
        headers={
            "X-Webhook-Event": "whatsapp.message.received",
            "X-Idempotency-Key": "idem-1",
        },
        json=kapso_webhook_payload(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["processed"] == 1
    assert "guard" in body["replies"][0]
    assert body["outbound_deliveries"] == [{"status": "skipped", "reason": "missing_api_key"}]


def test_kapso_webhook_ignores_other_events(client) -> None:
    response = client.post(
        "/webhooks/kapso",
        headers={"X-Webhook-Event": "whatsapp.message.delivered"},
        json=kapso_webhook_payload(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "processed": 0,
        "event": "whatsapp.message.delivered",
        "status": "ignored",
    }


def test_kapso_webhook_rejects_invalid_signature(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "kapso_webhook_secret", "test-secret")

    response = client.post(
        "/webhooks/kapso",
        headers={
            "X-Webhook-Event": "whatsapp.message.received",
            "X-Webhook-Signature": "invalid",
        },
        json=kapso_webhook_payload(),
    )

    assert response.status_code == 401


def test_kapso_webhook_accepts_valid_signature(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "kapso_webhook_secret", "test-secret")
    body = json.dumps(kapso_webhook_payload()).encode("utf-8")
    signature = hmac.new(b"test-secret", body, sha256).hexdigest()

    response = client.post(
        "/webhooks/kapso",
        headers={
            "X-Webhook-Event": "whatsapp.message.received",
            "X-Webhook-Signature": signature,
            "Content-Type": "application/json",
        },
        content=body,
    )

    assert response.status_code == 200
    assert response.json()["processed"] == 1
