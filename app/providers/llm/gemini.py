from pathlib import Path
from typing import Any

from app.providers.llm.base import LLMProvider
from app.schemas.assistant import AssistantRequest
from app.schemas.intents import ReminderIntentExtraction


class GeminiLLMProvider(LLMProvider):
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float,
        prompts_dir: Path | None = None,
        client: Any | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._prompts_dir = prompts_dir or Path("app/assistant/prompts")
        self._client = client

    def extract_reminder_intent(
        self,
        *,
        request: AssistantRequest,
    ) -> ReminderIntentExtraction:
        client = self._client or self._build_client()
        prompt = self._build_prompt(request)
        interaction = client.interactions.create(
            model=self._model,
            input=prompt,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": ReminderIntentExtraction.model_json_schema(),
            },
            timeout=self._timeout_seconds,
        )

        output_text = getattr(interaction, "output_text", None)
        if not isinstance(output_text, str) or not output_text.strip():
            raise ValueError("Gemini response did not include output_text.")
        return ReminderIntentExtraction.model_validate_json(output_text)

    def _build_client(self) -> Any:
        from google import genai

        return genai.Client(api_key=self._api_key)

    def _build_prompt(self, request: AssistantRequest) -> str:
        system_prompt = self._read_prompt("system.md")
        reminder_prompt = self._read_prompt("reminder_parser.md")
        return (
            f"{system_prompt}\n\n"
            f"{reminder_prompt}\n\n"
            "Interpret this normalized assistant request as structured JSON.\n"
            f"business_id: {request.business_id}\n"
            f"channel: {request.channel}\n"
            f"timestamp: {request.timestamp.isoformat()}\n"
            f"text: {request.text or ''}"
        )

    def _read_prompt(self, filename: str) -> str:
        return (self._prompts_dir / filename).read_text(encoding="utf-8").strip()
