from pathlib import Path

from app.providers.llm.base import LLMProvider
from app.schemas.assistant import AssistantRequest
from app.schemas.intents import ReminderIntentExtraction


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
        reminder_prompt = self._read_prompt("reminder_parser.md")

        response = client.responses.parse(
            model=self._model,
            instructions=f"{system_prompt}\n\n{reminder_prompt}",
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

    def _read_prompt(self, filename: str) -> str:
        return (self._prompts_dir / filename).read_text(encoding="utf-8").strip()
