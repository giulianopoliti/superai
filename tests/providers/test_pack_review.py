import logging
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace

import pytest
from pypdf import PdfReader, PdfWriter

from app.modules.procurement.pack_review import (
    PackReviewBatch,
    PackReviewDecision,
    apply_pack_review,
)
from app.modules.procurement.schemas import ExtractedSupplierOfferItem, SupplierOfferExtraction
from app.modules.procurement.supplier_offers import SupplierOfferService
from app.providers.documents.pack_review import OpenAIPackReviewer, review_extraction_packs


def wipes():
    return ExtractedSupplierOfferItem(
        raw_name="KIMBIES Toallas humedas x48u",
        offer_price=1500,
        unit_size=48,
        unit="u",
        package_quantity=48,
        price_type="pack",
        page_number=2,
        provider_observations={"gemini": {"price_type": "pack", "package_quantity": 48}},
    )


def decision(**updates):
    data = dict(
        price_type="unit",
        package_quantity=None,
        retail_contents_count=48,
        confidence=0.98,
        evidence="Kimbies toallitas humedas x48u",
        reason="Un sobre contiene 48 toallitas",
    )
    return PackReviewDecision(**(data | updates))


def test_reviewed_retail_contents_never_become_commercial_pack_on_persistence():
    item = wipes()
    apply_pack_review(item, decision())
    data = SupplierOfferService._item_dict_from_extracted_item(item, [])
    saved = SupplierOfferService._build_item(
        business_id="b",
        supplier_id="s",
        document_id="d",
        item=data,
    )
    assert saved.package_quantity is None
    assert saved.unit_size == 48
    assert saved.price_type == "unit"
    assert saved.comparable_unit_price == Decimal(1500)
    assert saved.metadata["provider_observations"]["gemini"]["package_quantity"] == 48
    assert saved.metadata["pack_review"]["decision"]["retail_contents_count"] == 48


@pytest.mark.parametrize(
    "updates",
    [
        {"confidence": 0.5},
        {"price_type": "unknown"},
        {"evidence": " "},
        {"price_type": "pack", "package_quantity": None},
        {"price_type": "pack", "package_quantity": -6},
        {"price_type": "unit", "package_quantity": 48},
    ],
)
def test_uncertain_or_inconsistent_review_preserves_price_without_dividing(updates):
    item = wipes()
    apply_pack_review(item, decision(**updates))
    assert item.price_type == "unknown"
    assert item.package_quantity is None
    assert item.offer_price == 1500
    assert item.pack_review["status"] == "needs_review"


def test_real_commercial_pack_requires_explicit_quantity():
    item = wipes()
    item.raw_name = "Gaseosas pack 6 botellas"
    apply_pack_review(
        item,
        decision(price_type="pack", package_quantity=6, evidence="Precio por pack de 6 botellas"),
    )
    assert item.price_type == "pack"
    assert item.package_quantity == 6
    assert item.offer_price == 1500


def test_reviewer_receives_only_original_page_and_failures_keep_offer(tmp_path, caplog):
    path = tmp_path / "original.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=200, height=100)
    writer.write(path)
    seen = []

    class Reviewer:
        def review(self, *, item, attachment, mime):
            page = PdfReader(BytesIO(attachment))
            assert len(page.pages) == 1
            assert page.pages[0].mediabox.width == 200
            seen.append(item.raw_name)
            if len(seen) == 2:
                raise TimeoutError("temporary error")
            return decision()

    extraction = SupplierOfferExtraction(items=[wipes(), wipes()])
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        review_extraction_packs(extraction, document_path=path, reviewer=Reviewer())
    assert len(seen) == 2
    assert extraction.items[0].price_type == "unit"
    assert extraction.items[1].price_type == "unknown"
    assert extraction.document_metadata["pack_needs_review_count"] == 1
    assert extraction.document_metadata["pack_review_duration_seconds"] >= 0
    assert caplog.text.count("stage=item_completed") == 2
    assert "pack_review" in caplog.text
    assert "stage=completed" in caplog.text


def test_missing_reviewer_or_page_keeps_pack_unconfirmed(tmp_path):
    path = tmp_path / "original.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(path)
    item = wipes()  # points to missing page 2
    extraction = SupplierOfferExtraction(items=[item])
    review_extraction_packs(extraction, document_path=path, reviewer=object())
    assert item.pack_review["error"]["code"] == "missing_source_page"
    assert item.price_type == "unknown"


def test_openai_pack_review_uses_closed_schema(monkeypatch):
    calls = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["max_retries"] == 0
            self.responses = self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def parse(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(output_parsed=decision())

    monkeypatch.setattr("openai.OpenAI", Client)
    reviewed = OpenAIPackReviewer(api_key="test", model="test", timeout_seconds=10).review(
        item=wipes(),
        attachment=b"page",
        mime="application/pdf",
    )
    assert reviewed.price_type == "unit"
    assert calls[0]["text_format"] == PackReviewDecision


def test_batch_reviews_same_page_once_and_keeps_individual_decisions(tmp_path):
    path = tmp_path / "original.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=200, height=100)
    writer.write(path)
    calls = []

    class Reviewer:
        def review_many(self, *, items, attachment, mime):
            calls.append(items)
            assert len(PdfReader(BytesIO(attachment)).pages) == 1
            return [decision(), decision(confidence=0.5)]

    extraction = SupplierOfferExtraction(items=[wipes(), wipes()])
    review_extraction_packs(extraction, document_path=path, reviewer=Reviewer())
    assert len(calls) == 1
    assert extraction.items[0].pack_review["status"] == "confirmed"
    assert extraction.items[1].pack_review["status"] == "needs_review"
    assert extraction.document_metadata["pack_review_provider_calls"] == 1


@pytest.mark.parametrize("indices", [[0, 0], [0], [0, 2]])
def test_openai_batch_rejects_duplicate_missing_or_foreign_indices(monkeypatch, indices):
    batch = PackReviewBatch(
        decisions=[{"item_index": index, **decision().model_dump()} for index in indices]
    )
    reviewer = OpenAIPackReviewer(api_key="test", model="test", timeout_seconds=10)
    monkeypatch.setattr(reviewer, "_review", lambda **kwargs: batch)
    with pytest.raises(ValueError):
        reviewer.review_many(items=[wipes(), wipes()], attachment=b"page", mime="application/pdf")


def test_failed_batch_keeps_all_prices_unconfirmed(tmp_path):
    path = tmp_path / "original.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=100, height=100)
    writer.write(path)

    class Reviewer:
        calls = 0

        def review_many(self, **kwargs):
            self.calls += 1
            raise TimeoutError("test")

    reviewer = Reviewer()
    extraction = SupplierOfferExtraction(items=[wipes(), wipes()])
    review_extraction_packs(extraction, document_path=path, reviewer=reviewer)
    assert reviewer.calls == 1
    assert all(
        item.price_type == "unknown" and item.offer_price == 1500 for item in extraction.items
    )
    assert extraction.document_metadata["pack_needs_review_count"] == 2
