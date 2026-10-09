from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.assistant.agent.executor import AgentExecutionService
from app.assistant.agent.schemas import AgentPlan, AgentPlanMode, AgentStep
from app.assistant.tools.registry import AssistantToolRegistry
from app.assistant.tools.schemas import (
    AssistantToolName,
    AssistantToolResult,
    ToolExecutionContext,
)
from app.schemas.assistant import AssistantRequest, Channel, MessageType


class FakeTool:
    name = AssistantToolName.SALES_TOP_PRODUCTS

    def execute(
        self,
        *,
        arguments: dict[str, object],
        context: ToolExecutionContext,
    ) -> AssistantToolResult:
        assert context.business_id == "business-1"
        assert "business_id" not in arguments
        return AssistantToolResult(
            name=self.name,
            reply="resultado ventas",
            data={"ok": True},
            metadata={"arg_count": len(arguments)},
        )


def make_request() -> AssistantRequest:
    return AssistantRequest(
        channel=Channel.WEB,
        external_user_id="user-1",
        business_id="business-1",
        message_type=MessageType.TEXT,
        text="dame ventas",
        timestamp=datetime(2026, 8, 13, 12, 0, tzinfo=ZoneInfo("America/Buenos_Aires")),
    )


def test_agent_executor_runs_registered_tool_with_context_business_id() -> None:
    registry = AssistantToolRegistry([FakeTool()])
    executor = AgentExecutionService(tool_registry=registry)

    result = executor.execute(
        request=make_request(),
        plan=AgentPlan(
            mode=AgentPlanMode.EXECUTE_PLAN,
            steps=[
                AgentStep(
                    tool=AssistantToolName.SALES_TOP_PRODUCTS,
                    arguments={"limit": 5},
                )
            ],
            confidence=0.9,
        ),
    )

    assert result.reply == "resultado ventas"
    assert result.steps[0].data == {"ok": True}
    assert result.metadata["step_count"] == 1


def test_agent_executor_rejects_too_many_steps() -> None:
    registry = AssistantToolRegistry([FakeTool()])
    executor = AgentExecutionService(tool_registry=registry, max_steps=1)

    with pytest.raises(ValueError, match="too many steps"):
        executor.execute(
            request=make_request(),
            plan=AgentPlan(
                mode=AgentPlanMode.EXECUTE_PLAN,
                steps=[
                    AgentStep(tool=AssistantToolName.SALES_TOP_PRODUCTS),
                    AgentStep(tool=AssistantToolName.SALES_TOP_PRODUCTS),
                ],
                confidence=0.9,
            ),
        )
