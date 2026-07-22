import hmac
import json
from hashlib import sha256

from app.channels.whatsapp_kapso import KapsoWhatsAppAdapter, verify_kapso_signature
from app.schemas.assistant import Channel, MessageType


def kapso_text_event(text: str = "recordame comprar bolsas") -> dict[str, object]:
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


def test_kapso_adapter_normalizes_text_message() -> None:
    adapter = KapsoWhatsAppAdapter(business_id="business-1")

    requests = adapter.to_assistant_requests(kapso_text_event())

    assert len(requests) == 1
    assistant_request = requests[0]
    assert assistant_request.channel == Channel.WHATSAPP
    assert assistant_request.external_user_id == "541169405063"
    assert assistant_request.business_id == "business-1"
    assert assistant_request.message_type == MessageType.TEXT
    assert assistant_request.text == "recordame comprar bolsas"
    assert assistant_request.raw_payload["event"]["message"]["id"] == "wamid.test"


def test_kapso_adapter_normalizes_batched_messages() -> None:
    adapter = KapsoWhatsAppAdapter(business_id="business-1")
    payload = {
        "type": "whatsapp.message.received",
        "batch": True,
        "data": [
            kapso_text_event("recordame revisar la heladera"),
            kapso_text_event("listar recordatorios pendientes"),
        ],
    }

    requests = adapter.to_assistant_requests(payload)

    assert [request.text for request in requests] == [
        "recordame revisar la heladera",
        "listar recordatorios pendientes",
    ]


def test_kapso_adapter_uses_conversation_identity_from_real_webhook_shape() -> None:
    adapter = KapsoWhatsAppAdapter(business_id="business-1")
    payload = {
        "message": {
            "from": "541169405063",
            "id": "wamid.real",
            "text": {"body": "recordame comprar bolsas"},
            "timestamp": "1784727476",
            "type": "text",
        },
        "conversation": {
            "business_scoped_user_id": "AR.1787141432462124",
            "phone_number": "541169405063",
            "phone_number_id": "597907523413541",
        },
        "phone_number_id": "597907523413541",
        "type": "whatsapp.message.received",
    }

    requests = adapter.to_assistant_requests(payload)

    assert requests[0].external_user_id == "541169405063"


def test_kapso_adapter_normalizes_non_text_message_as_attachment() -> None:
    adapter = KapsoWhatsAppAdapter(business_id="business-1")
    payload = {
        "message": {
            "from": "541169405063",
            "id": "wamid.image",
            "image": {"id": "media-1", "mime_type": "image/jpeg"},
            "kapso": {
                "direction": "inbound",
                "phone_number": "541169405063",
                "phone_number_id": "597907523413541",
            },
            "timestamp": "1784651246",
            "type": "image",
        }
    }

    requests = adapter.to_assistant_requests(payload)

    assert requests[0].message_type == MessageType.IMAGE
    assert requests[0].text is None
    assert requests[0].attachments[0].metadata["id"] == "media-1"


def test_verify_kapso_signature_accepts_valid_signature() -> None:
    raw_body = json.dumps(kapso_text_event()).encode("utf-8")
    secret = "test-secret"
    signature = hmac.new(secret.encode("utf-8"), raw_body, sha256).hexdigest()

    assert verify_kapso_signature(raw_body, signature, secret)


def test_verify_kapso_signature_rejects_invalid_signature() -> None:
    raw_body = json.dumps(kapso_text_event()).encode("utf-8")

    assert not verify_kapso_signature(raw_body, "invalid", "test-secret")
