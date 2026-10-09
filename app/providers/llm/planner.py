from typing import Protocol

from app.assistant.agent.schemas import AgentPlan
from app.assistant.tools.schemas import AssistantToolDefinition
from app.schemas.assistant import AssistantRequest


class LLMPlannerProvider(Protocol):
    def build_agent_plan(
        self,
        *,
        request: AssistantRequest,
        tools: list[AssistantToolDefinition],
    ) -> AgentPlan:
        """Build a structured, backend-validated tool execution plan."""
