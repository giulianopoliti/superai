from app.assistant.agent.schemas import (
    AgentExecutionResult,
    AgentPlan,
    AgentPlanMode,
    AgentStepExecution,
)
from app.assistant.tools.registry import AssistantToolRegistry
from app.assistant.tools.schemas import ToolExecutionContext
from app.schemas.assistant import AssistantRequest


class AgentExecutionService:
    def __init__(
        self,
        *,
        tool_registry: AssistantToolRegistry,
        max_steps: int = 5,
    ) -> None:
        self._tool_registry = tool_registry
        self._max_steps = max_steps

    def execute(self, *, plan: AgentPlan, request: AssistantRequest) -> AgentExecutionResult:
        if plan.mode == AgentPlanMode.ANSWER_DIRECTLY:
            return AgentExecutionResult(
                plan=plan,
                reply=plan.direct_reply or "",
                metadata={"mode": plan.mode},
            )
        if plan.mode == AgentPlanMode.ASK_CLARIFICATION:
            return AgentExecutionResult(
                plan=plan,
                reply=plan.clarification_question or "Me falta un dato para avanzar.",
                metadata={"mode": plan.mode, "requires_clarification": True},
            )
        if len(plan.steps) > self._max_steps:
            raise ValueError(f"Agent plan has too many steps: {len(plan.steps)}")

        context = ToolExecutionContext(
            business_id=request.business_id,
            external_user_id=request.external_user_id,
            channel=str(request.channel),
            timestamp=request.timestamp,
        )
        executed_steps: list[AgentStepExecution] = []
        for index, step in enumerate(plan.steps, start=1):
            if not self._tool_registry.has_tool(step.tool):
                raise ValueError(f"Agent tool is not registered: {step.tool}")
            result = self._tool_registry.execute(
                name=step.tool,
                arguments=step.arguments,
                context=context,
            )
            executed_steps.append(
                AgentStepExecution(
                    step_index=index,
                    tool=result.name,
                    arguments=step.arguments,
                    reply=result.reply,
                    data=result.data,
                    metadata=result.metadata,
                )
            )

            if result.metadata.get("requires_clarification"):
                break

        reply = executed_steps[-1].reply if executed_steps else "No pude ejecutar la consulta."
        return AgentExecutionResult(
            plan=plan,
            steps=executed_steps,
            reply=reply,
            metadata={
                "mode": plan.mode,
                "step_count": len(executed_steps),
                "requires_clarification": bool(
                    executed_steps and executed_steps[-1].metadata.get("requires_clarification")
                ),
            },
        )
