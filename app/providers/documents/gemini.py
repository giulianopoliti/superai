from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep
from typing import Any

from app.modules.procurement.schemas import (
    SupplierOfferExtraction,
    SupplierOfferPageExtraction,
    SupplierOfferReaderExtraction,
)
from app.providers.documents.base import (
    DocumentExtractionProvider,
    DocumentPageContext,
    reader_extraction,
)


class GeminiSupplierOfferDocumentProvider(DocumentExtractionProvider):
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float,
        prompts_dir: Path | None = None,
        client: Any | None = None,
        retry_delay_seconds: float = 2.0,
        allow_direct_fallback: bool = True,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._prompts_dir = prompts_dir or Path("app/assistant/prompts")
        self._client = client
        self._retry_delay_seconds = retry_delay_seconds
        self._allow_direct_fallback = allow_direct_fallback

    def extract_supplier_offer(
        self,
        *,
        document_path: Path,
        supplier_hint: str | None = None,
        page_context: DocumentPageContext | None = None,
    ) -> SupplierOfferExtraction:
        if page_context is not None:
            return self._extract_chunk(document_path, supplier_hint, page_context)
        client = self._client or self._build_client()
        with TemporaryDirectory(prefix="stock-ai-gemini-pages-") as temp_dir:
            page_images = self._extract_image_only_pdf_pages(
                document_path=document_path,
                output_dir=Path(temp_dir),
            )
            if page_images:
                try:
                    page_extraction = self._extract_image_only_pdf(
                        client=client,
                        page_images=page_images,
                        document_path=document_path,
                        supplier_hint=supplier_hint,
                    )
                except Exception as exc:
                    if not self._allow_direct_fallback:
                        raise ValueError(
                            f"Gemini page extraction failed for {document_path.name}: {exc}"
                        ) from exc
                    return self._extract_whole_document_after_page_failure(
                        client=client,
                        document_path=document_path,
                        supplier_hint=supplier_hint,
                        reason=(
                            f"No pudimos leer bien el documento con la lectura por paginas: {exc}"
                        ),
                    )
                if (
                    self._allow_direct_fallback
                    and self._image_only_extraction_needs_direct_fallback(page_extraction)
                ):
                    try:
                        direct_extraction = self._extract_whole_document_after_page_failure(
                            client=client,
                            document_path=document_path,
                            supplier_hint=supplier_hint,
                            reason=(
                                "La lectura por paginas marco baja cobertura. "
                                "Vamos a intentar leerlo con Gemini directo sobre el PDF completo."
                            ),
                        )
                    except Exception:
                        page_extraction.warnings.append(
                            "La lectura por paginas marco baja cobertura y el fallback directo "
                            "con Gemini tambien fallo. Revisar manualmente."
                        )
                        return page_extraction
                    if len(direct_extraction.items) >= len(page_extraction.items):
                        return direct_extraction
                    page_extraction.warnings.append(
                        "La lectura por paginas marco baja cobertura. Se intento Gemini directo, "
                        "pero recupero menos productos que la lectura por paginas."
                    )
                return page_extraction

        return self._extract_whole_document(
            client=client,
            document_path=document_path,
            supplier_hint=supplier_hint,
        )

    def _extract_chunk(
        self,
        document_path: Path,
        supplier_hint: str | None,
        context: DocumentPageContext,
    ) -> SupplierOfferExtraction:
        from google.genai import types

        client = self._client or self._build_client()
        try:
            response = client.models.generate_content(
                model=self._model,
                contents=[
                    types.Part.from_bytes(
                        data=document_path.read_bytes(), mime_type="application/pdf"
                    ),
                    f"supplier_hint: {supplier_hint or ''}\n"
                    f"source_filename: {context.source_filename}\n"
                    f"Attached page count: {context.page_count}. Use local page numbers.",
                ],
                config={
                    "system_instruction": self._read_prompt("supplier_offer_chunk_extractor.md"),
                    "response_mime_type": "application/json",
                    "response_schema": SupplierOfferReaderExtraction,
                    "temperature": 0,
                },
            )
            parsed = SupplierOfferReaderExtraction.model_validate_json(response.text)
            extraction = reader_extraction(
                parsed,
                source_filename=context.source_filename,
                provider="gemini",
            )
            extraction.supplier_name = extraction.supplier_name or supplier_hint
            return extraction
        finally:
            if self._client is None:
                client.close()

    def _extract_whole_document(
        self,
        *,
        client: Any,
        document_path: Path,
        supplier_hint: str | None,
    ) -> SupplierOfferExtraction:
        uploaded_file = client.files.upload(file=str(document_path))
        output_text = self._generate_content_text(
            client=client,
            uploaded_file=uploaded_file,
            prompt_filename="supplier_offer_extractor.md",
            instruction=(
                "Extract supplier offer items from this document as JSON matching the "
                "provided schema.\n"
                f"supplier_hint: {supplier_hint or ''}\n"
                f"source_filename: {document_path.name}\n"
                f"json_schema: {SupplierOfferExtraction.model_json_schema()}"
            ),
        )
        parsed = SupplierOfferExtraction.model_validate_json(output_text)
        if parsed.source_filename is None:
            parsed.source_filename = document_path.name
        if parsed.supplier_name is None:
            parsed.supplier_name = supplier_hint
        return parsed

    def _extract_whole_document_after_page_failure(
        self,
        *,
        client: Any,
        document_path: Path,
        supplier_hint: str | None,
        reason: str,
    ) -> SupplierOfferExtraction:
        extraction = self._extract_whole_document(
            client=client,
            document_path=document_path,
            supplier_hint=supplier_hint,
        )
        extraction.warnings.insert(
            0,
            f"{reason} Resultado generado con Gemini directo; revisar antes de comprar.",
        )
        extraction.document_metadata = {
            **extraction.document_metadata,
            "extraction_mode": "whole_document_direct_fallback",
            "fallback_reason": reason,
        }
        return extraction

    def _extract_image_only_pdf(
        self,
        *,
        client: Any,
        page_images: list[tuple[int, Path]],
        document_path: Path,
        supplier_hint: str | None,
    ) -> SupplierOfferExtraction:
        page_extractions: list[SupplierOfferPageExtraction] = []
        items = []
        warnings: list[str] = []

        for page_number, image_path in page_images:
            uploaded_file = client.files.upload(file=str(image_path))
            try:
                output_text = self._generate_content_text(
                    client=client,
                    uploaded_file=uploaded_file,
                    prompt_filename="supplier_offer_page_extractor.md",
                    instruction=(
                        "Extract supplier offer items from this single page image as JSON "
                        "matching the provided schema.\n"
                        f"supplier_hint: {supplier_hint or ''}\n"
                        f"source_filename: {document_path.name}\n"
                        f"page_number: {page_number}\n"
                        f"json_schema: {SupplierOfferPageExtraction.model_json_schema()}"
                    ),
                )
            except Exception as exc:
                raise ValueError(
                    f"Gemini page {page_number} extraction failed for {document_path.name}: {exc}"
                ) from exc
            page_extraction = SupplierOfferPageExtraction.model_validate_json(output_text)
            page_extraction.page_number = page_number
            for item in page_extraction.items:
                item.page_number = item.page_number or page_number
            page_extraction.coverage.extracted_item_count = len(page_extraction.items)
            if self._page_needs_review(page_extraction):
                page_extraction.coverage.needs_review = True
            page_extractions.append(page_extraction)
            items.extend(page_extraction.items)
            warnings.extend(
                f"page {page_number}: {warning}" for warning in page_extraction.warnings
            )
            if page_extraction.coverage.needs_review:
                warnings.append(
                    f"page {page_number}: extraction coverage needs review "
                    f"({len(page_extraction.items)} items extracted)"
                )

        needs_review_pages = [
            page.page_number for page in page_extractions if page.coverage.needs_review
        ]
        return SupplierOfferExtraction(
            supplier_name=supplier_hint,
            source_filename=document_path.name,
            items=items,
            warnings=warnings,
            page_extractions=page_extractions,
            document_metadata={
                "extraction_mode": "image_only_pdf_pages",
                "page_count": len(page_images),
                "items_by_page": {
                    str(page.page_number): len(page.items) for page in page_extractions
                },
                "needs_review_pages": needs_review_pages,
            },
        )

    def _generate_content_text(
        self,
        *,
        client: Any,
        uploaded_file: Any,
        prompt_filename: str,
        instruction: str,
    ) -> str:
        prompt = self._read_prompt(prompt_filename)
        last_error: Exception | None = None
        error_summaries: list[str] = []
        for model in self._model_candidates():
            for attempt in range(1, 3):
                try:
                    response = client.models.generate_content(
                        model=model,
                        contents=[
                            uploaded_file,
                            instruction,
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
                    error_summaries.append(self._error_summary(model, attempt, exc))
                    if not self._should_try_next_model(exc):
                        raise
                    if attempt == 1:
                        sleep(self._retry_delay_seconds)
            else:
                continue
            break
        else:
            raise ValueError(
                "Gemini document extraction failed for all fallback models: "
                + " | ".join(error_summaries[-6:])
            ) from last_error

        output_text = getattr(response, "text", None)
        if not isinstance(output_text, str) or not output_text.strip():
            raise ValueError("Gemini response did not include JSON text.")
        return output_text

    @staticmethod
    def _page_needs_review(page_extraction: SupplierOfferPageExtraction) -> bool:
        estimated_blocks = page_extraction.coverage.estimated_offer_blocks
        extracted_count = len(page_extraction.items)
        if estimated_blocks is not None and extracted_count < max(1, estimated_blocks // 2):
            return True
        return extracted_count < 5 and estimated_blocks is not None and estimated_blocks >= 8

    @staticmethod
    def _image_only_extraction_needs_direct_fallback(
        extraction: SupplierOfferExtraction,
    ) -> bool:
        if not extraction.items:
            return True
        if any(page.coverage.needs_review for page in extraction.page_extractions):
            return True
        estimated_total = sum(
            page.coverage.estimated_offer_blocks or 0 for page in extraction.page_extractions
        )
        return estimated_total >= 10 and len(extraction.items) < estimated_total * 0.7

    @staticmethod
    def _extract_image_only_pdf_pages(
        *,
        document_path: Path,
        output_dir: Path,
    ) -> list[tuple[int, Path]]:
        if document_path.suffix.lower() != ".pdf":
            return []
        try:
            from pypdf import PdfReader
        except ImportError:
            return []

        try:
            reader = PdfReader(str(document_path))
        except Exception:
            return []

        page_images: list[tuple[int, Path]] = []
        for page_number, page in enumerate(reader.pages, start=1):
            try:
                text = (page.extract_text() or "").strip()
                images = list(page.images)
            except Exception:
                return []
            if text or len(images) != 1:
                return []
            image = images[0]
            image_path = output_dir / f"page_{page_number:03d}_{image.name}"
            image_path.write_bytes(image.data)
            page_images.append((page_number, image_path))
        return page_images

    def _build_client(self) -> Any:
        from google import genai

        return genai.Client(
            api_key=self._api_key,
            http_options={"timeout": int(self._timeout_seconds * 1000)},
        )

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

    @staticmethod
    def _error_summary(model: str, attempt: int, exc: Exception) -> str:
        status_code = getattr(exc, "status_code", None)
        prefix = f"{model} attempt {attempt}"
        if status_code:
            prefix = f"{prefix} status {status_code}"
        message = " ".join(str(exc).split())
        if len(message) > 220:
            message = message[:217] + "..."
        return f"{prefix}: {message}"

    def _read_prompt(self, filename: str) -> str:
        return (self._prompts_dir / filename).read_text(encoding="utf-8").strip()
