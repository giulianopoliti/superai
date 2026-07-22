from app.assistant.intent_router import IntentRouter
from app.providers.llm.base import LLMProvider
from app.schemas.assistant import AssistantRequest
from app.schemas.intents import IntentName, IntentResult, ReminderIntentExtraction


class LLMIntentRouter:
    def __init__(
        self,
        *,
        llm_provider: LLMProvider,
        fallback_router: IntentRouter | None = None,
        minimum_confidence: float = 0.7,
    ) -> None:
        self._llm_provider = llm_provider
        self._fallback_router = fallback_router or IntentRouter()
        self._minimum_confidence = minimum_confidence

    def route(self, request: AssistantRequest) -> IntentResult:
        if not request.text:
            return self._fallback_router.route(request)

        try:
            extraction = self._llm_provider.extract_reminder_intent(request=request)
        except Exception:
            return self._fallback_router.route(request)

        if extraction.intent in {
            IntentName.LIST_REMINDERS,
            IntentName.MARK_REMINDER_DONE,
            IntentName.UNKNOWN,
        }:
            return self._to_intent_result(extraction)

        if extraction.intent != IntentName.CREATE_REMINDER:
            return self._fallback_router.route(request)

        if extraction.requires_clarification or extraction.confidence < self._minimum_confidence:
            return self._to_intent_result(extraction, requires_clarification=True)

        if not extraction.title:
            return IntentResult(
                intent=IntentName.CREATE_REMINDER,
                confidence=extraction.confidence,
                requires_clarification=True,
                clarification_question="Que queres que te recuerde?",
            )

        return self._to_intent_result(extraction)

    @staticmethod
    def _to_intent_result(
        extraction: ReminderIntentExtraction,
        *,
        requires_clarification: bool | None = None,
    ) -> IntentResult:
        return IntentResult(
            intent=extraction.intent,
            entities={
                "title": extraction.title,
                "due_at": extraction.due_at,
                "timezone": extraction.timezone,
                "raw_time_expression": extraction.raw_time_expression,
            },
            confidence=extraction.confidence,
            requires_clarification=(
                extraction.requires_clarification
                if requires_clarification is None
                else requires_clarification
            ),
            clarification_question=extraction.clarification_question,
        )
