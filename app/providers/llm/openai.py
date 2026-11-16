import json
from pathlib import Path

from pydantic import BaseModel

from app.assistant.agent.schemas import AgentPlan
from app.assistant.tools.schemas import AssistantToolDefinition, AssistantToolName
from app.providers.llm.base import LLMProvider
from app.schemas.assistant import AssistantRequest
from app.schemas.intents import ReminderIntentExtraction


class _OpenAIPlanStep(BaseModel):
    tool: AssistantToolName
    arguments_json: str


class _OpenAIPlan(BaseModel):
    mode: str
    steps: list[_OpenAIPlanStep]
    direct_reply: str | None
    clarification_question: str | None
    confidence: float


class OpenAILLMProvider(LLMProvider):
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float,
        prompts_dir: Path | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._prompts_dir = prompts_dir or Path("app/assistant/prompts")

    def extract_reminder_intent(
        self,
        *,
        request: AssistantRequest,
    ) -> ReminderIntentExtraction:
        from openai import OpenAI

        client = OpenAI(api_key=self._api_key, timeout=self._timeout_seconds)
        system_prompt = self._read_prompt("system.md")
        intent_prompt = self._read_prompt("intent_classifier.md")
        reminder_prompt = self._read_prompt("reminder_parser.md")

        response = client.responses.parse(
            model=self._model,
            instructions=f"{system_prompt}\n\n{intent_prompt}\n\n{reminder_prompt}",
            input=[
                {
                    "role": "user",
                    "content": (
                        "Interpret this normalized assistant request as structured JSON.\n"
                        f"business_id: {request.business_id}\n"
                        f"channel: {request.channel}\n"
                        f"timestamp: {request.timestamp.isoformat()}\n"
                        f"text: {request.text or ''}"
                    ),
                }
            ],
            text_format=ReminderIntentExtraction,
        )

        parsed = response.output_parsed
        if not isinstance(parsed, ReminderIntentExtraction):
            raise ValueError("OpenAI response did not match ReminderIntentExtraction.")
        return parsed

    def build_agent_plan(
        self,
        *,
        request: AssistantRequest,
        tools: list[AssistantToolDefinition],
    ) -> AgentPlan:
        from openai import OpenAI

        client = OpenAI(api_key=self._api_key, timeout=self._timeout_seconds)
        # OpenAI strict schemas cannot express an arbitrary dict[str, Any].
        # Transport arguments as JSON text, then validate them through the tool's
        # schema in the executor as usual.
        response = client.responses.parse(
            model=self._model,
            instructions=(
                self._read_prompt("system.md") + "\n\n"
                + self._read_prompt("agent_planner.md") + "\n\n"
                + self._read_prompt("agent_planner_openai.md")
            ),
            input=json.dumps({
                "request": request.model_dump(mode="json"),
                "conversation_context": request._planner_context,
                "available_tools": [tool.model_dump(mode="json") for tool in tools],
            }, ensure_ascii=False),
            text_format=_OpenAIPlan,
        )
        parsed = response.output_parsed
        if not isinstance(parsed, _OpenAIPlan):
            raise ValueError("OpenAI response did not match the agent plan schema.")
        return AgentPlan.model_validate({
            **parsed.model_dump(exclude={"steps"}),
            "steps": [
                {"tool": step.tool, "arguments": json.loads(step.arguments_json)}
                for step in parsed.steps
            ],
        })

    def _read_prompt(self, filename: str) -> str:
        return (self._prompts_dir / filename).read_text(encoding="utf-8").strip()
