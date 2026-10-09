from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field, model_validator

from app.modules.procurement.analysis_contracts import (
    DocumentReadResult,
    ProviderCallRecord,
    ReaderBlocksExtractionV2,
    ReaderStructuredExtractionV2,
    TokenUsage,
)
from app.modules.procurement.schemas import (
    ExtractedSupplierOfferItem,
    SupplierOfferExtraction,
    SupplierOfferPageExtraction,
    SupplierOfferReaderExtraction,
)


class DocumentPageContext(BaseModel):
    source_filename: str
    start_page: int = Field(ge=1)
    end_page: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_range(self):
        if self.end_page < self.start_page:
            raise ValueError("end_page must be >= start_page")
        return self

    @property
    def page_count(self) -> int:
        return self.end_page - self.start_page + 1

    def original_page(self, local_page: int | None) -> int | None:
        if local_page is None or not 1 <= local_page <= self.page_count:
            return None
        return self.start_page + local_page - 1


class DocumentExtractionProvider(Protocol):
    def extract_supplier_offer(
        self,
        *,
        document_path: Path,
        supplier_hint: str | None = None,
        page_context: DocumentPageContext | None = None,
    ) -> SupplierOfferExtraction:
        """Return local page numbers; the orchestrator maps them to source pages once."""


def reader_extraction(
    parsed: SupplierOfferReaderExtraction,
    *,
    source_filename: str,
    provider: str,
) -> SupplierOfferExtraction:
    """Flatten the wire format while retaining reported coverage and missing page IDs."""
    items = []
    pages = []
    for page in parsed.page_extractions:
        page_items = [ExtractedSupplierOfferItem(**item.model_dump()) for item in page.items]
        for item in page_items:
            item.provider_observations = {
                provider: item.model_dump(
                    mode="json",
                    exclude={
                        "agreement_score",
                        "agreement_status",
                        "selected_source",
                        "resolved_by",
                        "provider_observations",
                        "pack_review",
                    },
                )
            }
            if page.page_number is not None and item.page_number != page.page_number:
                item.page_number = None
        items.extend(page_items)
        pages.append(
            SupplierOfferPageExtraction(
                page_number=page.page_number or 0,
                items=page_items,
                warnings=page.warnings,
                coverage=page.coverage,
            )
        )
    return SupplierOfferExtraction(
        supplier_name=parsed.supplier_name,
        source_filename=source_filename,
        items=items,
        page_extractions=pages,
        warnings=parsed.warnings,
        document_metadata={"reader_provider": provider},
    )


def response_token_usage(response: object) -> TokenUsage | None:
    """Capture available SDK counts without adding cached/reasoning subsets twice."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return None

    def count(source: object, name: str) -> int | None:
        value = source.get(name) if isinstance(source, dict) else getattr(source, name, None)
        return value if type(value) is int and value >= 0 else None

    def details(name: str) -> object:
        return usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)

    return TokenUsage(
        input_tokens=count(usage, "input_tokens"),
        cached_input_tokens=count(details("input_tokens_details"), "cached_tokens"),
        output_tokens=count(usage, "output_tokens"),
        reasoning_output_tokens=count(details("output_tokens_details"), "reasoning_tokens"),
        total_tokens=count(usage, "total_tokens"),
    )


def document_read_result(
    *,
    response: object,
    call: ProviderCallRecord,
    text_format: type[ReaderStructuredExtractionV2] | type[ReaderBlocksExtractionV2],
) -> DocumentReadResult:
    """Return a failure envelope too, preserving billed usage before JSON validation.

    Pages stay local. The analysis service converts pages and assigns evidence IDs.
    """
    response_id = getattr(response, "id", None)
    call.response_id = response_id if isinstance(response_id, str) else None
    model = getattr(response, "model", None)
    if isinstance(model, str) and model:
        call.model = model
    call.usage = response_token_usage(response)
    status = getattr(response, "status", None)
    refusal = any(
        getattr(content, "type", None) == "refusal"
        for output in getattr(response, "output", None) or []
        for content in getattr(output, "content", None) or []
    )
    if status == "incomplete":
        call.status, call.safe_error = "incomplete", "openai_incomplete"
    elif refusal:
        call.status, call.safe_error = "refusal", "openai_refusal"
    elif status not in {None, "completed"}:
        call.status, call.safe_error = "error", "openai_response_not_completed"
    else:
        try:
            # Raw create() keeps response/usage accessible even on malformed JSON.
            text = getattr(response, "output_text", None)
            if not isinstance(text, str) or not text:
                raise ValueError("Missing structured text")
            extraction = text_format.model_validate_json(text)
            call.status = "success"
            return DocumentReadResult(extraction=extraction, call=call)
        except ValueError:
            call.status, call.safe_error = "error", "openai_invalid_structured_output"
    return DocumentReadResult(extraction=None, call=call)
