import pytest
from pydantic import ValidationError

from app.schemas.assistant import AssistantRequest, AssistantResponse, Channel, MessageType


def test_assistant_request_requires_business_id() -> None:
    with pytest.raises(ValidationError):
        AssistantRequest(
            channel=Channel.CLI,
            external_user_id="user-1",
            business_id="",
            message_type=MessageType.TEXT,
            text="hola",
        )


def test_assistant_request_requires_text_or_attachment() -> None:
    with pytest.raises(ValidationError):
        AssistantRequest(
            channel=Channel.CLI,
            external_user_id="user-1",
            business_id="business-1",
            message_type=MessageType.TEXT,
        )


def test_assistant_response_defaults() -> None:
    response = AssistantResponse(reply="Listo")

    assert response.actions == []
    assert response.requires_confirmation is False
    assert response.confirmation_payload is None
    assert response.metadata == {}
