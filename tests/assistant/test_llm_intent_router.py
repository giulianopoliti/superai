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


def test_llm_router_falls_back_when_provider_returns_unknown() -> None:
    router = LLMIntentRouter(
        llm_provider=FakeLLMProvider(
            ReminderIntentExtraction(intent=IntentName.UNKNOWN, confidence=0.2)
        ),
        fallback_router=IntentRouter(),
    )

    result = router.route(make_request("Decime los productos mas vendidos de los ultimos 7 dias"))

    assert result.intent == IntentName.QUERY_SALES_TOP_PRODUCTS


def test_llm_router_extracts_sales_top_products_entities() -> None:
    router = LLMIntentRouter(
        llm_provider=FakeLLMProvider(
            ReminderIntentExtraction(
                intent=IntentName.QUERY_SALES_TOP_PRODUCTS,
                family="almacen",
                period="all_time",
                limit=3,
                rank_from=2,
                rank_to=4,
                days=7,
                sort="quantity",
                exclude_product_names=["hielo", "bolsa pan"],
                confidence=0.91,
            )
        ),
        fallback_router=IntentRouter(),
    )

    result = router.route(
        make_request("Dame los 3 productos mas vendidos de almacen excluyendo hielo y bolsa pan")
    )

    assert result.intent == IntentName.QUERY_SALES_TOP_PRODUCTS
    assert result.entities["family"] == "almacen"
    assert result.entities["period"] == "all_time"
    assert result.entities["limit"] == 3
    assert result.entities["rank_from"] == 2
    assert result.entities["rank_to"] == 4
    assert result.entities["exclude_product_names"] == ["hielo", "bolsa pan"]


def test_llm_router_uses_deterministic_due_at_when_llm_omits_it() -> None:
    router = LLMIntentRouter(
        llm_provider=FakeLLMProvider(
            ReminderIntentExtraction(
                intent=IntentName.CREATE_REMINDER,
                title="me prepare la ropa",
                due_at=None,
                confidence=0.93,
            )
        ),
        fallback_router=IntentRouter(),
    )

    result = router.route(
        make_request("Haceme acordar en 2 minutos que me prepare la ropa por favor")
    )

    assert result.intent == IntentName.CREATE_REMINDER
    assert result.entities["title"] == "me prepare la ropa"
    assert result.entities["due_at"] == "2026-07-22T12:26:00-03:00"
