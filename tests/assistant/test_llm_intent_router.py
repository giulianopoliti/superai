from datetime import datetime
from zoneinfo import ZoneInfo

from app.assistant.intent_router import IntentRouter
from app.assistant.llm_intent_router import LLMIntentRouter
from app.schemas.assistant import AssistantRequest, Channel, MessageType
from app.schemas.intents import IntentName, ReminderIntentExtraction


class FakeLLMProvider:
    def __init__(self, extraction: ReminderIntentExtraction | None = None) -> None:
        self.extraction = extraction

    def extract_reminder_intent(
        self,
        *,
        request: AssistantRequest,
    ) -> ReminderIntentExtraction:
        if self.extraction is None:
            raise RuntimeError("LLM unavailable")
        return self.extraction


def make_request(text: str) -> AssistantRequest:
    return AssistantRequest(
        channel=Channel.WHATSAPP,
        external_user_id="541169405063",
        business_id="business-1",
        message_type=MessageType.TEXT,
        text=text,
        timestamp=datetime(2026, 7, 22, 12, 24, tzinfo=ZoneInfo("America/Buenos_Aires")),
    )


def test_llm_router_extracts_argentina_time_format() -> None:
    router = LLMIntentRouter(
        llm_provider=FakeLLMProvider(
            ReminderIntentExtraction(
                intent=IntentName.CREATE_REMINDER,
                title="si no puedo concentrarme me ponga a escribir",
                due_at="2026-07-23T12:30:00-03:00",
                confidence=0.93,
                raw_time_expression="a las 12.30",
            )
        )
    )

    result = router.route(
        make_request("recordame a las 12.30 que si no puedo concentrarme me ponga a escribir")
    )

    assert result.intent == IntentName.CREATE_REMINDER
    assert result.entities["title"] == "si no puedo concentrarme me ponga a escribir"
    assert result.entities["due_at"] == "2026-07-23T12:30:00-03:00"
    assert not result.requires_clarification


def test_llm_router_requests_clarification_on_low_confidence() -> None:
    router = LLMIntentRouter(
        llm_provider=FakeLLMProvider(
            ReminderIntentExtraction(
                intent=IntentName.CREATE_REMINDER,
                title=None,
                confidence=0.4,
                requires_clarification=True,
                clarification_question="Que queres que te recuerde?",
            )
        )
    )

    result = router.route(make_request("recordame"))

    assert result.requires_clarification
    assert result.clarification_question == "Que queres que te recuerde?"


def test_llm_router_falls_back_when_provider_fails() -> None:
    router = LLMIntentRouter(
        llm_provider=FakeLLMProvider(),
        fallback_router=IntentRouter(),
    )

    result = router.route(make_request("recordame revisar la camara"))

    assert result.intent == IntentName.CREATE_REMINDER
    assert result.entities["title"] == "revisar la camara"
