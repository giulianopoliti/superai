import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.assistant.agent.schemas import AgentPlanMode
from app.assistant.tools.schemas import AssistantToolName, TopProductsQuery
from app.providers.llm.openai import OpenAILLMProvider
from app.schemas.assistant import AssistantRequest
from app.schemas.intents import ReminderIntentExtraction


def request():
    return AssistantRequest(
        channel="web", business_id="shop", external_user_id="user", message_type="text",
        text="Más vendidos familia LIMPIEZA con proveedor SINA si hay vínculo",
        timestamp=datetime(2026, 10, 22, tzinfo=UTC),
    )


def mock_openai(monkeypatch, output):
    calls = []

    def parse(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(output_parsed=kwargs["text_format"].model_validate(output))

    monkeypatch.setattr("openai.OpenAI", lambda **kwargs: SimpleNamespace(
        responses=SimpleNamespace(parse=parse),
    ))
    provider = OpenAILLMProvider(api_key="test", model="configured-chat-model", timeout_seconds=6)
    return provider, calls


def test_openai_classifier_loads_sales_prompt_and_preserves_filters(monkeypatch):
    provider, calls = mock_openai(monkeypatch, {
        "intent": "query_sales_top_products", "family": "limpieza", "supplier": "sina",
        "supplier_optional": True, "confidence": 0.95,
    })
    result = provider.extract_reminder_intent(request=request())
    assert result.family == "limpieza"
    assert result.supplier == "sina"
    assert result.supplier_optional
    assert "Intent Classifier Prompt v3" in calls[0]["instructions"]
    assert calls[0]["model"] == "configured-chat-model"


def test_intent_quantity_schema_uses_supported_numeric_constraints():
    # Regression for a real OpenAI 400: Decimal generated an unsupported
    # lookaround regex, causing every chat request to use the regex fallback.
    from openai.lib._pydantic import to_strict_json_schema

    schema = to_strict_json_schema(ReminderIntentExtraction)
    assert schema["properties"]["min_quantity"]["anyOf"] == [
        {"minimum": 0, "type": "number"}, {"type": "null"},
    ]
    with pytest.raises(ValidationError):
        ReminderIntentExtraction(intent="query_sales_top_products", confidence=1, min_quantity=-1)


@pytest.mark.parametrize("arguments", [
    '{"family":"limpieza","supplier":"sina","supplier_optional":true}',
    '[]', '{broken',
])
def test_openai_planner_validates_argument_transport(monkeypatch, arguments):
    provider, calls = mock_openai(monkeypatch, {
        "mode": "execute_plan", "steps": [{
            "tool": "sales.top_products", "arguments_json": arguments,
        }], "direct_reply": None, "clarification_question": None, "confidence": 0.95,
    })
    if arguments.startswith('{"family"'):
        plan = provider.build_agent_plan(request=request(), tools=[])
        assert plan.mode == AgentPlanMode.EXECUTE_PLAN
        assert plan.steps[0].tool == AssistantToolName.SALES_TOP_PRODUCTS
        query = TopProductsQuery.model_validate(plan.steps[0].arguments)
        assert query.family == "limpieza"
        assert query.supplier_optional
        assert json.loads(calls[0]["input"])["request"]["business_id"] == "shop"
    else:
        with pytest.raises((ValueError, ValidationError)):
            provider.build_agent_plan(request=request(), tools=[])
