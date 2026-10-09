from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.assistant.tools.schemas import AssistantToolName


class AgentPlanMode(StrEnum):
    ANSWER_DIRECTLY = "answer_directly"
    ASK_CLARIFICATION = "ask_clarification"
    EXECUTE_PLAN = "execute_plan"


class AgentStep(BaseModel):
    tool: AssistantToolName
    arguments: dict[str, Any] = Field(default_factory=dict)


class AgentPlan(BaseModel):
    mode: AgentPlanMode = AgentPlanMode.EXECUTE_PLAN
    steps: list[AgentStep] = Field(default_factory=list)
    direct_reply: str | None = None
    clarification_question: str | None = None
    confidence: float = Field(default=0, ge=0, le=1)

    @model_validator(mode="after")
    def validate_mode_payload(self) -> "AgentPlan":
        if self.mode == AgentPlanMode.EXECUTE_PLAN and not self.steps:
            raise ValueError("execute_plan requires at least one step.")
        if self.mode == AgentPlanMode.ASK_CLARIFICATION and not self.clarification_question:
            raise ValueError("ask_clarification requires clarification_question.")
        if self.mode == AgentPlanMode.ANSWER_DIRECTLY and not self.direct_reply:
            raise ValueError("answer_directly requires direct_reply.")
        return self


class AgentStepExecution(BaseModel):
    step_index: int
    tool: AssistantToolName
    arguments: dict[str, Any] = Field(default_factory=dict)
    reply: str
    data: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentExecutionResult(BaseModel):
    plan: AgentPlan
    steps: list[AgentStepExecution] = Field(default_factory=list)
    reply: str
    metadata: dict[str, Any] = Field(default_factory=dict)
