import base64
import json
import logging
import mimetypes
from io import BytesIO
from pathlib import Path
from time import monotonic
from uuid import uuid4

from pypdf import PdfReader, PdfWriter

from app.modules.procurement.pack_review import (
    PackReviewBatch,
    PackReviewDecision,
    apply_pack_review,
    needs_pack_review,
)
from app.providers.documents.errors import provider_error

logger = logging.getLogger("uvicorn.error")


class OpenAIPackReviewer:
    def __init__(self, *, api_key, model, timeout_seconds):
        self._api_key, self._model, self._timeout = api_key, model, timeout_seconds

    def review(self, *, item, attachment, mime):
        return self._review(items=[item], attachment=attachment, mime=mime, batch=False)

    def review_many(self, *, items, attachment, mime):
        result = self._review(items=items, attachment=attachment, mime=mime, batch=True)
        indices = [decision.item_index for decision in result.decisions]
        if sorted(indices) != list(range(len(items))):
            raise ValueError("Pack review must return each item index exactly once")
        return [
            PackReviewDecision.model_validate(decision.model_dump(exclude={"item_index"}))
            for decision in sorted(result.decisions, key=lambda decision: decision.item_index)
        ]

    def _review(self, *, items, attachment, mime, batch):
        from openai import OpenAI

        prompt = Path("app/assistant/prompts/supplier_pack_review.md").read_text(encoding="utf-8")
        encoded = base64.b64encode(attachment).decode("ascii")
        content = (
            {"type": "input_image", "image_url": f"data:{mime};base64,{encoded}"}
            if mime.startswith("image/")
            else {
                "type": "input_file",
                "filename": "offer-page.pdf",
                "file_data": f"data:application/pdf;base64,{encoded}",
            }
        )
        with OpenAI(api_key=self._api_key, timeout=self._timeout, max_retries=0) as client:
            response = client.responses.parse(
                model=self._model,
                instructions=prompt,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": json.dumps(
                                    [
                                        {
                                            "item_index": index,
                                            **item.model_dump(
                                                mode="json",
                                                include={
                                                    "raw_name",
                                                    "offer_price",
                                                    "price_type",
                                                    "package_quantity",
                                                    "notes",
                                                },
                                            ),
                                        }
                                        for index, item in enumerate(items)
                                    ],
                                    ensure_ascii=False,
                                ),
                            },
                            content,
                        ],
                    }
                ],
                text_format=PackReviewBatch if batch else PackReviewDecision,
            )
        if not isinstance(response.output_parsed, PackReviewBatch if batch else PackReviewDecision):
            raise ValueError("Pack review did not return a structured decision")
        return response.output_parsed


def review_extraction_packs(extraction, *, document_path, reviewer):
    started_at = monotonic()
    review_id = uuid4().hex[:12]
    targets = [item for item in extraction.items if needs_pack_review(item)]
    if not targets:
        extraction.document_metadata["pack_review_duration_seconds"] = 0
        return extraction
    logger.info(
        "pack_review review_id=%s stage=started target_count=%s",
        review_id,
        len(targets),
    )
    mime = mimetypes.guess_type(document_path.name)[0] or "application/octet-stream"
    pdf = PdfReader(BytesIO(document_path.read_bytes())) if mime == "application/pdf" else None
    attachment_cache = {}
    reviewed = {}
    provider_calls = 0
    disabled_error = None
    for index, item in enumerate(targets, start=1):
        item_started_at = monotonic()
        error, decision = None, None
        if reviewer is None:
            error = {"code": "pack_reviewer_unavailable"}
        elif disabled_error:
            error = disabled_error
        elif pdf and (item.page_number is None or not 1 <= item.page_number <= len(pdf.pages)):
            error = {"code": "missing_source_page"}
        elif not pdf and not mime.startswith("image/"):
            error = {"code": "unsupported_pack_evidence"}
        else:
            try:
                key = item.page_number if pdf else 0
                if key not in attachment_cache:
                    if pdf:
                        output = BytesIO()
                        writer = PdfWriter()
                        writer.add_page(pdf.pages[key - 1])
                        writer.write(output)
                        writer.close()
                        attachment_cache[key] = output.getvalue()
                    else:
                        attachment_cache[key] = document_path.read_bytes()
                if hasattr(reviewer, "review_many"):
                    if index not in reviewed:
                        group = [
                            (position, candidate)
                            for position, candidate in enumerate(targets, start=1)
                            if position not in reviewed
                            and candidate.page_number == item.page_number
                        ][:8]
                        provider_calls += 1
                        try:
                            decisions = reviewer.review_many(
                                items=[candidate for _, candidate in group],
                                attachment=attachment_cache[key],
                                mime=mime,
                            )
                            if len(decisions) != len(group):
                                raise ValueError("Incomplete pack review batch")
                            for (position, _), answer in zip(group, decisions, strict=True):
                                reviewed[position] = (answer, None)
                        except Exception as exc:
                            batch_error = provider_error(exc)
                            for position, _ in group:
                                reviewed[position] = (None, batch_error)
                            if not batch_error["retryable"]:
                                disabled_error = batch_error
                    decision, error = reviewed[index]
                else:
                    provider_calls += 1
                    decision = reviewer.review(
                        item=item, attachment=attachment_cache[key], mime=mime
                    )
            except Exception as exc:
                error = provider_error(exc)
                if not error["retryable"]:
                    disabled_error = error
        apply_pack_review(item, decision, error=error)
        logger.info(
            "pack_review review_id=%s stage=item_completed item_index=%s page=%s "
            "duration_ms=%.1f status=%s",
            review_id,
            index,
            item.page_number or "unknown",
            (monotonic() - item_started_at) * 1000,
            item.pack_review["status"],
        )
    pending = sum(item.pack_review["status"] == "needs_review" for item in targets)
    extraction.document_metadata["pack_review_count"] = len(targets)
    extraction.document_metadata["pack_review_provider_calls"] = provider_calls
    extraction.document_metadata["pack_needs_review_count"] = pending
    extraction.document_metadata["pack_review_duration_seconds"] = round(
        monotonic() - started_at, 3
    )
    logger.info(
        "pack_review review_id=%s stage=completed duration_ms=%.1f target_count=%s "
        "pending_count=%s",
        review_id,
        (monotonic() - started_at) * 1000,
        len(targets),
        pending,
    )
    if pending:
        extraction.warnings.append(
            f"{pending} ofertas requieren revisar la base del precio o bulto."
        )
    return extraction
