import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.providers.llm.gemini import GeminiLLMProvider
from app.schemas.assistant import AssistantRequest, Channel, MessageType
from app.schemas.intents import IntentName


class FakeInteraction:
    output_text = json.dumps(
        {
            "intent": "create_reminder",
            "title": "si no puedo concentrarme me ponga a escribir",
            "due_at": "2026-07-23T12:30:00-03:00",
            "timezone": "America/Buenos_Aires",
            "confidence": 0.93,
            "requires_clarification": False,
            "clarification_question": None,
            "raw_time_expression": "a las 12.30",
        }
    )


class FakeInteractions:
    def __init__(self) -> None:
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return FakeInteraction()


class FakeGeminiClient:
    def __init__(self) -> None:
        self.interactions = FakeInteractions()


def test_gemini_provider_requests_structured_output(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "system.md").write_text("System prompt", encoding="utf-8")
    (prompts_dir / "reminder_parser.md").write_text("Reminder prompt", encoding="utf-8")
    client = FakeGeminiClient()
    provider = GeminiLLMProvider(
        api_key="test-key",
        model="gemini-3.6-flash",
        timeout_seconds=10,
        prompts_dir=prompts_dir,
        client=client,
    )

    extraction = provider.extract_reminder_intent(request=make_request())

    assert extraction.intent == IntentName.CREATE_REMINDER
    assert extraction.title == "si no puedo concentrarme me ponga a escribir"
    assert extraction.due_at == "2026-07-23T12:30:00-03:00"
    assert client.interactions.last_kwargs["model"] == "gemini-3.6-flash"
    assert client.interactions.last_kwargs["response_format"]["mime_type"] == "application/json"
    assert client.interactions.last_kwargs["response_format"]["schema"]["title"] == (
        "ReminderIntentExtraction"
    )


def make_request() -> AssistantRequest:
    return AssistantRequest(
        channel=Channel.WHATSAPP,
        external_user_id="541169405063",
        business_id="business-1",
        message_type=MessageType.TEXT,
        text="recordame a las 12.30 que si no puedo concentrarme me ponga a escribir",
        timestamp=datetime(2026, 7, 22, 12, 24, tzinfo=ZoneInfo("America/Buenos_Aires")),
    )
