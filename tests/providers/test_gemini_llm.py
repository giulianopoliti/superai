import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.assistant.tools.schemas import AssistantToolDefinition, AssistantToolName
from app.providers.llm.gemini import GeminiLLMProvider
from app.schemas.assistant import AssistantRequest, Channel, MessageType
from app.schemas.intents import IntentName

REMINDER_OUTPUT = {
    "intent": "create_reminder",
    "title": "si no puedo concentrarme me ponga a escribir",
    "due_at": "2026-07-23T12:30:00-03:00",
    "timezone": "America/Buenos_Aires",
    "confidence": 0.93,
    "requires_clarification": False,
    "clarification_question": None,
    "raw_time_expression": "a las 12.30",
}

PLAN_OUTPUT = {
    "mode": "execute_plan",
    "steps": [
        {
            "tool": "sales.top_products",
            "arguments": {"families": ["almacen"], "limit": 5},
        }
    ],
    "confidence": 0.91,
}


class FakeInteraction:
    def __init__(self, output: dict[str, object]) -> None:
        self.output_text = json.dumps(output)


class FakeInteractions:
    def __init__(self, output: dict[str, object]) -> None:
        self._output = output
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return FakeInteraction(self._output)


class FakeGeminiClient:
    def __init__(self, output: dict[str, object]) -> None:
        self.interactions = FakeInteractions(output)


def test_gemini_provider_requests_structured_output(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "system.md").write_text("System prompt", encoding="utf-8")
    (prompts_dir / "intent_classifier.md").write_text("Intent prompt", encoding="utf-8")
    (prompts_dir / "reminder_parser.md").write_text("Reminder prompt", encoding="utf-8")
    client = FakeGeminiClient(REMINDER_OUTPUT)
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
    assert "Intent prompt" in client.interactions.last_kwargs["input"]


def test_gemini_provider_builds_agent_plan_with_structured_output(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "system.md").write_text("System prompt", encoding="utf-8")
    (prompts_dir / "agent_planner.md").write_text("Agent planner prompt", encoding="utf-8")
    client = FakeGeminiClient(PLAN_OUTPUT)
    provider = GeminiLLMProvider(
        api_key="test-key",
        model="gemini-3.6-flash",
        timeout_seconds=10,
        prompts_dir=prompts_dir,
        client=client,
    )

    plan = provider.build_agent_plan(
        request=make_request(),
        tools=[
            AssistantToolDefinition(
                name=AssistantToolName.SALES_TOP_PRODUCTS,
                description="Ventas top",
            )
        ],
    )

    assert plan.steps[0].tool == AssistantToolName.SALES_TOP_PRODUCTS
    assert plan.steps[0].arguments["families"] == ["almacen"]
    assert client.interactions.last_kwargs["response_format"]["schema"]["title"] == "AgentPlan"
    assert "Agent planner prompt" in client.interactions.last_kwargs["input"]
    assert "available_tools" in client.interactions.last_kwargs["input"]


def make_request() -> AssistantRequest:
    return AssistantRequest(
        channel=Channel.WHATSAPP,
        external_user_id="541169405063",
        business_id="business-1",
        message_type=MessageType.TEXT,
        text="recordame a las 12.30 que si no puedo concentrarme me ponga a escribir",
        timestamp=datetime(2026, 7, 22, 12, 24, tzinfo=ZoneInfo("America/Buenos_Aires")),
    )
