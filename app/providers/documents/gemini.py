from pathlib import Path
from typing import Any

from app.modules.procurement.schemas import SupplierOfferExtraction
from app.providers.documents.base import DocumentExtractionProvider


class GeminiSupplierOfferDocumentProvider(DocumentExtractionProvider):
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

    def extract_supplier_offer(
        self,
        *,
        document_path: Path,
        supplier_hint: str | None = None,
    ) -> SupplierOfferExtraction:
        client = self._client or self._build_client()
        prompt = self._read_prompt("supplier_offer_extractor.md")
        uploaded_file = client.files.upload(file=str(document_path))
        last_error: Exception | None = None
        for model in self._model_candidates():
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=[
                        uploaded_file,
                        (
                            "Extract supplier offer items from this document as JSON matching the "
                            "provided schema.\n"
                            f"supplier_hint: {supplier_hint or ''}\n"
                            f"source_filename: {document_path.name}\n"
                            f"json_schema: {SupplierOfferExtraction.model_json_schema()}"
                        ),
                    ],
                    config={
                        "system_instruction": prompt,
                        "response_mime_type": "application/json",
                        "temperature": 0,
                    },
                )
                break
            except Exception as exc:
                last_error = exc
                if not self._should_try_next_model(exc):
                    raise
        else:
            raise ValueError(
                "Gemini document extraction failed for all fallback models."
            ) from last_error

        output_text = getattr(response, "text", None)
        if not isinstance(output_text, str) or not output_text.strip():
            raise ValueError("Gemini response did not include JSON text.")

        parsed = SupplierOfferExtraction.model_validate_json(output_text)
        if parsed.source_filename is None:
            parsed.source_filename = document_path.name
        if parsed.supplier_name is None:
            parsed.supplier_name = supplier_hint
        return parsed

    def _build_client(self) -> Any:
        from google import genai

        return genai.Client(api_key=self._api_key)

    def _model_candidates(self) -> tuple[str, ...]:
        fallback_models = (
            "gemini-flash-latest",
            "gemini-3.5-flash",
            "gemini-3.1-flash-lite",
        )
        seen: set[str] = set()
        models: list[str] = []
        for model in (self._model, *fallback_models):
            if model not in seen:
                seen.add(model)
                models.append(model)
        return tuple(models)

    @staticmethod
    def _should_try_next_model(exc: Exception) -> bool:
        status_code = getattr(exc, "status_code", None)
        if status_code in {404, 429, 500, 502, 503, 504}:
            return True
        error_text = str(exc).upper()
        return any(code in error_text for code in ("429", "500", "502", "503", "504"))

    def _read_prompt(self, filename: str) -> str:
        return (self._prompts_dir / filename).read_text(encoding="utf-8").strip()
