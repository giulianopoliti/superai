from pathlib import Path

from app.modules.procurement.schemas import SupplierOfferExtraction
from app.providers.documents.base import DocumentExtractionProvider


class OpenAISupplierOfferDocumentProvider(DocumentExtractionProvider):
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

    def extract_supplier_offer(
        self,
        *,
        document_path: Path,
        supplier_hint: str | None = None,
    ) -> SupplierOfferExtraction:
        from openai import OpenAI

        client = OpenAI(api_key=self._api_key, timeout=self._timeout_seconds)
        prompt = self._read_prompt("supplier_offer_extractor.md")
        uploaded_file = client.files.create(file=document_path, purpose="user_data")

        response = client.responses.parse(
            model=self._model,
            instructions=prompt,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Extract supplier offer items from this document.\n"
                                f"supplier_hint: {supplier_hint or ''}\n"
                                f"source_filename: {document_path.name}"
                            ),
                        },
                        {"type": "input_file", "file_id": uploaded_file.id},
                    ],
                }
            ],
            text_format=SupplierOfferExtraction,
        )

        parsed = response.output_parsed
        if not isinstance(parsed, SupplierOfferExtraction):
            raise ValueError("OpenAI response did not match SupplierOfferExtraction.")
        if parsed.source_filename is None:
            parsed.source_filename = document_path.name
        if parsed.supplier_name is None:
            parsed.supplier_name = supplier_hint
        return parsed

    def _read_prompt(self, filename: str) -> str:
        return (self._prompts_dir / filename).read_text(encoding="utf-8").strip()
