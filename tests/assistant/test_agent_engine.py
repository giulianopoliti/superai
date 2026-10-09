from datetime import datetime
from zoneinfo import ZoneInfo

from app.assistant.agent.executor import AgentExecutionService
from app.assistant.agent.schemas import AgentPlan, AgentPlanMode, AgentStep
from app.assistant.engine import AssistantEngine
from app.assistant.intent_router import IntentRouter
from app.assistant.tools.registry import AssistantToolRegistry
from app.assistant.tools.schemas import (
    AssistantToolDefinition,
    AssistantToolName,
    AssistantToolResult,
    ToolExecutionContext,
)
from app.db.repositories.conversations import InMemoryConversationMessageRepository
from app.db.repositories.reminders import InMemoryReminderRepository
from app.modules.reminders.service import ReminderService
from app.schemas.assistant import AssistantRequest, Channel, MessageType


class FakePlanner:
    def build_agent_plan(
        self,
        *,
        request: AssistantRequest,
        tools: list[AssistantToolDefinition],
    ) -> AgentPlan:
        assert tools
        return AgentPlan(
            mode=AgentPlanMode.EXECUTE_PLAN,
            steps=[
                AgentStep(
                    tool=AssistantToolName.SALES_TOP_PRODUCTS,
                    arguments={"families": ["almacen", "verduleria"], "limit": 5},
                )
            ],
            confidence=0.9,
        )


class FakeSalesTool:
    name = AssistantToolName.SALES_TOP_PRODUCTS

    def execute(
        self,
        *,
        arguments: dict[str, object],
        context: ToolExecutionContext,
    ) -> AssistantToolResult:
        return AssistantToolResult(
            name=self.name,
            reply="Productos mas vendidos: AZUCAR, BANANA.",
            data={"arguments": arguments, "business_id": context.business_id},
            metadata={"product_count": 2},
        )


def make_request() -> AssistantRequest:
    return AssistantRequest(
        channel=Channel.WEB,
        external_user_id="user-1",
        business_id="business-1",
        message_type=MessageType.TEXT,
        text="Dame los 5 mas vendidos de almacen y verduleria",
        timestamp=datetime(2026, 8, 13, 12, 0, tzinfo=ZoneInfo("America/Buenos_Aires")),
    )


def test_engine_uses_agent_when_planner_and_executor_are_configured() -> None:
    registry = AssistantToolRegistry([FakeSalesTool()])
    engine = AssistantEngine(
        intent_router=IntentRouter(),
        reminder_service=ReminderService(InMemoryReminderRepository()),
        conversation_repository=InMemoryConversationMessageRepository(),
        tool_registry=registry,
        agent_planner=FakePlanner(),
        agent_executor=AgentExecutionService(tool_registry=registry),
        agent_minimum_confidence=0.7,
    )

    response = engine.handle_message(make_request())

    assert response.reply == "Productos mas vendidos: AZUCAR, BANANA."
    assert response.actions[0].type == "agent.step_executed"
    assert response.metadata["agent"]["steps"][0]["data"]["business_id"] == "business-1"
