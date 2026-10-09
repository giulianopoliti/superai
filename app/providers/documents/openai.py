import base64
import logging
import mimetypes
import re
from pathlib import Path
from time import monotonic
from uuid import uuid4

from pydantic import BaseModel

from app.modules.procurement.analysis_contracts import (
    WIRE_VERSION,
    AnalysisMethod,
    DocumentReadRequest,
    DocumentReadResult,
    ProviderCallRecord,
    ReaderBlocksExtractionV2,
    ReaderStructuredExtractionV2,
)
from app.modules.procurement.schemas import SupplierOfferExtraction, SupplierOfferReaderExtraction
from app.providers.documents.base import (
    DocumentExtractionProvider,
    DocumentPageContext,
    document_read_result,
    reader_extraction,
)

logger = logging.getLogger("uvicorn.error")


def _safe_request_id(value):
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        return value
    return None


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
        page_context: DocumentPageContext | None = None,
    ) -> SupplierOfferExtraction:
        prompt_name = (
            "supplier_offer_chunk_extractor.md" if page_context else "supplier_offer_extractor.md"
        )
        source_filename = page_context.source_filename if page_context else document_path.name
        instruction = (
            f"supplier_hint: {supplier_hint or ''}\nsource_filename: {source_filename}\n"
            "Return page_extractions with items, local page_number, coverage and warnings."
        )
        if page_context:
            instruction += f"\nAttached page count: {page_context.page_count}."
        response = self._inline_response(
            document_path=document_path,
            prompt_name=prompt_name,
            instruction=instruction,
            text_format=SupplierOfferReaderExtraction,
            timeout_seconds=self._timeout_seconds,
            legacy_parse=True,
        )
        parsed = response.output_parsed
        if not isinstance(parsed, SupplierOfferReaderExtraction):
            raise ValueError("OpenAI response did not match SupplierOfferReaderExtraction.")
        extraction = reader_extraction(parsed, source_filename=source_filename, provider="openai")
        extraction.supplier_name = extraction.supplier_name or supplier_hint
        return extraction

    def _inline_response(
        self,
        *,
        document_path: Path,
        prompt_name: str,
        instruction: str,
        text_format: type[BaseModel],
        timeout_seconds: float,
        legacy_parse: bool = False,
    ):
        from openai import OpenAI
        from openai.lib._pydantic import to_strict_json_schema

        prompt = (self._prompts_dir / prompt_name).read_text(encoding="utf-8").strip()
        encoded = base64.b64encode(document_path.read_bytes()).decode("ascii")
        mime = mimetypes.guess_type(document_path.name)[0] or "application/pdf"
        content = (
            {"type": "input_image", "image_url": f"data:{mime};base64,{encoded}"}
            if mime.startswith("image/")
            else {
                "type": "input_file",
                "filename": document_path.name,
                "file_data": f"data:{mime};base64,{encoded}",
            }
        )
        arguments = {
            "model": self._model,
            "instructions": prompt,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": instruction},
                        content,
                    ],
                }
            ],
        }
        # A single inline generation. V2 captures the raw response before parsing.
        with OpenAI(api_key=self._api_key, timeout=timeout_seconds, max_retries=0) as client:
            if legacy_parse:
                return client.responses.parse(**arguments, text_format=text_format)
            return client.responses.create(
                **arguments,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": text_format.__name__,
                        "strict": True,
                        "schema": to_strict_json_schema(text_format),
                    }
                },
            )

    def read(self, *, request: DocumentReadRequest) -> DocumentReadResult:
        """Read A or B through shared contracts, with local pages and failure metrics.

        No catalog decisions, page recovery, ID mapping or database access occur here.
        """
        structured = request.analysis_method == AnalysisMethod.OPENAI_STRUCTURED
        prompt_name = (
            "supplier_offer_structured_v3.md" if structured else "supplier_offer_blocks_v1.md"
        )
        text_format = ReaderStructuredExtractionV2 if structured else ReaderBlocksExtractionV2
        call = ProviderCallRecord(
            call_id=str(uuid4()),
            run_id=request.run_id,
            reading_id=request.reading_id,
            provider="openai",
            model=self._model,
            prompt_version=Path(prompt_name).stem,
            schema_version=WIRE_VERSION,
            analysis_method=request.analysis_method,
            phase=request.phase,
            start_page=request.start_page,
            end_page=request.end_page,
            status="error",
        )
        started = monotonic()
        effective_timeout = min(self._timeout_seconds, request.timeout_seconds)
        error_type = cause_type = request_id = None
        logger.info(
            "openai_reader started run_id=%s reading_id=%s method=%s pages=%s-%s "
            "model=%s timeout_seconds=%.3f file_bytes=%s",
            request.run_id, request.reading_id, request.analysis_method.value,
            request.start_page, request.end_page, self._model, effective_timeout,
            request.document_path.stat().st_size if request.document_path.is_file() else None,
        )
        try:
            context = DocumentPageContext(
                source_filename=request.source_filename,
                start_page=request.start_page,
                end_page=request.end_page,
            )
            instruction = (
                f"supplier_hint: {request.supplier_hint or ''}\n"
                f"source_filename: {request.source_filename}\n"
                f"Attached page count: {context.page_count}.\n"
                "Return pages with blocks, local page_number, page_conditions and coverage."
            )
            response = self._inline_response(
                document_path=request.document_path,
                prompt_name=prompt_name,
                instruction=instruction,
                text_format=text_format,
                timeout_seconds=effective_timeout,
            )
            request_id = _safe_request_id(getattr(response, "_request_id", None))
            return document_read_result(response=response, call=call, text_format=text_format)
        except Exception as exc:
            # Exception messages may contain credentials, document text or request bodies.
            call.status = "error"
            call.safe_error = f"openai_read_failed:{type(exc).__name__}"
            error_type = type(exc).__name__
            cause_type = type(exc.__cause__).__name__ if exc.__cause__ else None
            request_id = _safe_request_id(getattr(exc, "request_id", None))
            return DocumentReadResult(extraction=None, call=call)
        finally:
            call.duration_seconds = monotonic() - started
            logger.info(
                "openai_reader finished run_id=%s reading_id=%s pages=%s-%s status=%s "
                "duration_seconds=%.3f timeout_seconds=%.3f error_type=%s cause_type=%s "
                "request_id=%s response_id=%s usage_known=%s",
                request.run_id, request.reading_id, request.start_page, request.end_page,
                call.status, call.duration_seconds, effective_timeout, error_type, cause_type,
                request_id, _safe_request_id(call.response_id), call.usage is not None,
            )
