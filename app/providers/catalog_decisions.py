"""OpenAI Decisions transport, independent from catalog persistence."""

import json
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field


class ChoiceProbability(BaseModel):
    value: str
    probability: float = Field(ge=0, le=1)


class CatalogAnswer(BaseModel):
    name: str
    type: str
    choice: str | None = None
    probabilities: list[ChoiceProbability] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)


class DecisionUsage(BaseModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(default=0, ge=0)


class CatalogDecisionResponse(BaseModel):
    model: str
    answers: list[CatalogAnswer]
    usage: DecisionUsage


class CatalogDecisionsHTTPError(RuntimeError):
    def __init__(self, status_code, details):
        super().__init__(f"OpenAI Decisions HTTP {status_code}")
        self.status_code = status_code
        self.details = details


class OpenAICatalogDecisions:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gpt-6-luna",
        timeout: float = 45,
        prompt_path: Path | None = None,
        max_attempts: int = 3,
        on_response=None,
    ):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        if not 1 <= max_attempts <= 3:
            raise ValueError("Invalid transport attempt limit")
        self.max_attempts = max_attempts
        self.on_response = on_response
        self.prompt = (
            prompt_path
            or (Path(__file__).parents[1] / "assistant/prompts/catalog_interpretation_decisions.md")
        ).read_text(encoding="utf-8")

    def decide(self, source: dict, questions: list[dict]) -> tuple[dict, CatalogDecisionResponse]:
        payload = {
            "model": self.model,
            "input": self.prompt
            + "\nDATOS DEL ARTÍCULO:\n"
            + json.dumps({k: v for k, v in source.items() if k != "category"}, ensure_ascii=False),
            "questions": questions,
        }
        request = Request(
            "https://api.openai.com/v1/decisions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"},
        )
        for attempt in range(1, self.max_attempts + 1):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    raw = json.loads(response.read())
                raw["transport_attempts"] = attempt
                break
            except HTTPError as exc:
                if exc.code in {429, 500, 502, 503, 504} and attempt < self.max_attempts:
                    time.sleep(attempt)
                    continue
                # Do not include headers, keys or raw upstream errors in operator logs.
                try:
                    error = json.loads(exc.read()).get("error", {})
                    details = {k: error.get(k) for k in ("code", "param", "message")}
                except (ValueError, AttributeError):
                    details = {}
                raise CatalogDecisionsHTTPError(exc.code, details) from None
        if self.on_response:
            self.on_response(raw)
        return raw, CatalogDecisionResponse.model_validate(raw)
