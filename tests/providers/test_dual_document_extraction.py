import logging
from decimal import Decimal

from app import main
from app.modules.procurement.schemas import ExtractedSupplierOfferItem, SupplierOfferExtraction
from app.providers.documents.dual import DualDocumentExtractionProvider
from app.settings import settings


class FakeProvider:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = 0

    def extract_supplier_offer(self, *, document_path, supplier_hint=None):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def result(*items: ExtractedSupplierOfferItem) -> SupplierOfferExtraction:
    return SupplierOfferExtraction(supplier_name="Vital", items=list(items))


def offer(name: str, price: str, page: int = 1) -> ExtractedSupplierOfferItem:
    return ExtractedSupplierOfferItem(
        raw_name=name,
        offer_price=Decimal(price),
        page_number=page,
    )


def test_both_readers_run_and_agreed_rows_are_merged_once(tmp_path, caplog) -> None:
    gemini = FakeProvider(result=result(offer("Aceite 900 ml", "1000")))
    openai = FakeProvider(result=result(offer("ACEITE 900 ML", "1000")))
    provider = DualDocumentExtractionProvider([("gemini", gemini), ("openai", openai)])

    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        extraction = provider.extract_supplier_offer(document_path=tmp_path / "offer.jpg")

    assert gemini.calls == openai.calls == 1
    assert len(extraction.items) == 1
    assert extraction.document_metadata["agreed_item_count"] == 1
    assert extraction.document_metadata["conflict_count"] == 0
    assert extraction.document_metadata["extraction_id"]
    assert "stage=provider_completed provider=gemini" in caplog.text
    assert "stage=provider_completed provider=openai" in caplog.text
    assert "stage=completed" in caplog.text
    assert "confirmed_by_dual_read" in (extraction.items[0].notes or "")


def test_uses_remaining_reader_when_the_other_fails(tmp_path) -> None:
    gemini = FakeProvider(error=RuntimeError("503 UNAVAILABLE"))
    openai = FakeProvider(result=result(offer("Aceite", "1000")))
    provider = DualDocumentExtractionProvider([("gemini", gemini), ("openai", openai)])

    extraction = provider.extract_supplier_offer(document_path=tmp_path / "offer.jpg")

    assert gemini.calls == openai.calls == 1
    assert len(extraction.items) == 1
    assert extraction.document_metadata["successful_extraction_providers"] == ["openai"]
    assert extraction.document_metadata["failed_extraction_providers"] == ["gemini"]


def test_conflicting_prices_are_flagged_and_not_imported(tmp_path) -> None:
    gemini = FakeProvider(result=result(offer("Aceite", "1000")))
    openai = FakeProvider(result=result(offer("Aceite", "1200")))
    provider = DualDocumentExtractionProvider([("gemini", gemini), ("openai", openai)])

    extraction = provider.extract_supplier_offer(document_path=tmp_path / "offer.jpg")

    assert extraction.items == []
    assert extraction.document_metadata["conflict_count"] == 1
    assert any("Conflicto entre lectores" in warning for warning in extraction.warnings)


def test_keeps_offer_seen_by_only_one_reader(tmp_path) -> None:
    gemini = FakeProvider(result=result(offer("Aceite", "1000"), offer("Arroz", "900")))
    openai = FakeProvider(result=result(offer("Aceite", "1000")))
    provider = DualDocumentExtractionProvider([("gemini", gemini), ("openai", openai)])

    extraction = provider.extract_supplier_offer(document_path=tmp_path / "offer.jpg")

    assert {item.raw_name for item in extraction.items} == {"Aceite", "Arroz"}
    assert extraction.document_metadata["single_source_item_count"] == 1


def test_auto_builder_only_configures_openai(monkeypatch) -> None:
    monkeypatch.setattr(settings, "gemini_api_key", "gemini-test")
    monkeypatch.setattr(settings, "openai_api_key", "openai-test")

    provider = main.build_supplier_offer_document_provider("auto")

    assert [name for name, _ in provider._providers] == ["openai"]
    assert provider._chunk_size <= 2


def test_single_reader_does_not_reread_healthy_pages(tmp_path):
    from app.providers.documents.dual import ChunkedDocumentExtractionProvider

    reader = ChunkProvider(low_page=3)
    provider = ChunkedDocumentExtractionProvider([("openai", reader)], chunk_size=2)
    extraction = provider.extract_supplier_offer(document_path=make_pdf(tmp_path, 4))
    assert sorted(reader.calls) == [(1, 2), (3, 3), (3, 4)]
    assert extraction.document_metadata["retried_pages"] == [3]
    assert extraction.document_metadata["extraction_mode"] == "single_source_chunks"
    assert extraction.document_metadata["initial_provider_calls"] == 2
    assert extraction.document_metadata["retry_provider_calls"] == 1
    assert extraction.document_metadata["jev_shadow_count"] == 0
    assert all(item.agreement_status == "single_source" for item in extraction.items)


def test_auto_works_without_gemini_key_and_explicit_gemini_is_suspended(monkeypatch):
    import pytest
    from fastapi import HTTPException

    monkeypatch.setattr(settings, "gemini_api_key", None)
    monkeypatch.setattr(settings, "openai_api_key", "test")
    assert len(main.build_supplier_offer_document_provider("auto")._providers) == 1
    with pytest.raises(HTTPException) as error:
        main.build_supplier_offer_document_provider("gemini")
    assert error.value.status_code == 400


def test_pdf_is_read_in_one_chunk_by_both_providers(tmp_path) -> None:
    from pypdf import PdfWriter

    document = tmp_path / "offer.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    writer.add_blank_page(width=300, height=300)
    with document.open("wb") as output:
        writer.write(output)
    gemini = ChunkProvider()
    openai = ChunkProvider()
    provider = DualDocumentExtractionProvider([("gemini", gemini), ("openai", openai)])
    extraction = provider.extract_supplier_offer(document_path=document)
    assert len(gemini.calls) == len(openai.calls) == 1
    assert [item.page_number for item in extraction.items] == [1, 2]
    assert extraction.document_metadata["page_count"] == 2
    assert extraction.document_metadata["items_by_page"] == {"1": 1, "2": 1}


class ChunkProvider:
    def __init__(self, *, low_page=None, fail=False, tracker=None, invalid=False):
        self.calls = []
        self.low_page, self.fail, self.tracker, self.invalid = low_page, fail, tracker, invalid

    def extract_supplier_offer(self, *, document_path, supplier_hint=None, page_context=None):
        from pypdf import PdfReader

        from app.modules.procurement.schemas import (
            SupplierOfferPageCoverage,
            SupplierOfferPageExtraction,
        )

        self.calls.append((page_context.start_page, page_context.end_page))
        assert len(PdfReader(document_path).pages) == page_context.page_count
        if self.tracker:
            self.tracker.enter()
        try:
            if self.fail:
                raise TimeoutError("reader failed")
            pages = []
            for local in range(1, page_context.page_count + 1):
                page = page_context.start_page + local - 1
                low = self.low_page == page and page_context.page_count > 1
                items = [offer(f"Producto pagina {page}", "100", local)]
                if self.invalid:
                    items[0].page_number = 99
                if self.low_page == page and page_context.page_count == 1:
                    items.append(offer("Otra oferta recuperada", "200", 1))
                pages.append(
                    SupplierOfferPageExtraction(
                        page_number=local,
                        items=items,
                        coverage=SupplierOfferPageCoverage(
                            estimated_offer_blocks=8 if low else len(items)
                        ),
                    )
                )
            return SupplierOfferExtraction(
                supplier_name="Vital",
                items=[item for page in pages for item in page.items],
                page_extractions=pages,
            )
        finally:
            if self.tracker:
                self.tracker.leave()


def make_pdf(tmp_path, count):
    from pypdf import PdfWriter

    path = tmp_path / "offers.pdf"
    writer = PdfWriter()
    for _ in range(count):
        writer.add_blank_page(width=100, height=100)
    writer.write(path)
    return path


def test_invalid_schema_disables_reader_without_rereading_healthy_pages(tmp_path):
    import httpx
    from openai import BadRequestError

    class BrokenReader:
        calls = 0

        def extract_supplier_offer(self, **kwargs):
            self.calls += 1
            raise BadRequestError(
                "Invalid JSON schema: regex lookaround is not supported",
                response=httpx.Response(400, request=httpx.Request("POST", "https://test")),
                body={"code": "invalid_json_schema", "param": "text.format.schema"},
            )

    broken, healthy = BrokenReader(), ChunkProvider()
    result = DualDocumentExtractionProvider(
        [
            ("openai", broken),
            ("gemini", healthy),
        ]
    ).extract_supplier_offer(document_path=make_pdf(tmp_path, 27))
    assert 1 <= broken.calls <= 2  # only already-running blocks may also fail
    assert len(healthy.calls) == 5
    assert result.document_metadata["retried_pages"] == []
    assert len(result.document_metadata["needs_review_pages"]) == 27
    assert result.document_metadata["disabled_providers"]["openai"]["code"] == "invalid_json_schema"
    assert len(result.items) == 27


class ActiveTracker:
    def __init__(self):
        from threading import Barrier, Lock

        self.lock, self.barrier = Lock(), Barrier(4, timeout=5)
        self.active = self.peak = self.calls = 0

    def enter(self):
        from time import sleep

        with self.lock:
            self.active += 1
            self.calls += 1
            initial = self.calls <= 4
            self.peak = max(self.peak, self.active)
        if initial:
            self.barrier.wait()
        sleep(0.005)

    def leave(self):
        with self.lock:
            self.active -= 1


def test_27_pages_five_calls_per_reader_and_four_active_requests(tmp_path):
    tracker = ActiveTracker()
    a, b = ChunkProvider(tracker=tracker), ChunkProvider(tracker=tracker)
    extracted = DualDocumentExtractionProvider(
        [("gemini", a), ("openai", b)]
    ).extract_supplier_offer(
        document_path=make_pdf(tmp_path, 27),
    )
    assert sorted(a.calls) == sorted(b.calls) == [(1, 6), (7, 12), (13, 18), (19, 24), (25, 27)]
    assert [item.page_number for item in extracted.items] == list(range(1, 28))
    assert tracker.peak == extracted.document_metadata["peak_active_requests"] == 4
    assert extracted.document_metadata["initial_provider_calls"] == 10
    assert extracted.document_metadata["chunk_count"] == 5
    assert extracted.document_metadata["retried_pages"] == []
    assert extracted.document_metadata["retry_provider_calls"] == 0
    assert extracted.items[6].provider_observations["gemini"]["page_number"] == 1


def test_low_coverage_retries_only_suspicious_page_once_and_supplements(tmp_path):
    a, b = ChunkProvider(low_page=14), ChunkProvider(low_page=14)
    extracted = DualDocumentExtractionProvider(
        [("gemini", a), ("openai", b)]
    ).extract_supplier_offer(
        document_path=make_pdf(tmp_path, 27),
    )
    assert a.calls.count((14, 14)) == b.calls.count((14, 14)) == 1
    assert len(a.calls) == len(b.calls) == 6
    assert extracted.document_metadata["retried_pages"] == [14]
    assert extracted.document_metadata["retry_provider_calls"] == 2
    assert extracted.document_metadata["items_by_page"]["14"] == 2
    assert extracted.document_metadata["low_coverage_pages"] == [14]
    assert extracted.document_metadata["needs_review_pages"] == []
    assert len(extracted.items) == 28


def test_failure_of_either_reader_preserves_the_other(tmp_path):
    for failed in ("gemini", "openai"):
        readers = [(name, ChunkProvider(fail=name == failed)) for name in ("gemini", "openai")]
        extracted = DualDocumentExtractionProvider(readers).extract_supplier_offer(
            document_path=make_pdf(tmp_path, 3),
        )
        assert len(extracted.items) == 3
        assert extracted.document_metadata["failed_pages_by_provider"][failed] == [1, 2, 3]
        assert extracted.document_metadata["retried_pages"] == [1, 2, 3]
        assert extracted.document_metadata["single_source_item_count"] == 3
        assert all(len(reader.calls) == 4 for _, reader in readers)


def test_invalid_page_numbers_are_not_guessed_and_get_review(tmp_path):
    extracted = DualDocumentExtractionProvider(
        [
            ("gemini", ChunkProvider(invalid=True)),
            ("openai", ChunkProvider()),
        ]
    ).extract_supplier_offer(document_path=make_pdf(tmp_path, 2))
    assert extracted.document_metadata["retried_pages"] == [1, 2]
    assert extracted.document_metadata["needs_review_pages"] == [1, 2]
    assert any(item.page_number is None for item in extracted.items)
    assert all(item.page_number in (None, 1, 2) for item in extracted.items)


def test_both_readers_fail_and_leave_review_metadata(tmp_path):
    extracted = DualDocumentExtractionProvider(
        [
            ("gemini", ChunkProvider(fail=True)),
            ("openai", ChunkProvider(fail=True)),
        ]
    ).extract_supplier_offer(document_path=make_pdf(tmp_path, 2))
    assert extracted.items == []
    assert extracted.document_metadata["needs_review_pages"] == [1, 2]
    assert extracted.document_metadata["retry_provider_calls"] == 4


def test_low_coverage_can_be_disabled(tmp_path):
    extracted = DualDocumentExtractionProvider(
        [
            ("gemini", ChunkProvider(low_page=1)),
            ("openai", ChunkProvider(low_page=1)),
        ],
        retry_low_coverage=False,
    ).extract_supplier_offer(document_path=make_pdf(tmp_path, 2))
    assert extracted.document_metadata["retried_pages"] == []
    assert extracted.document_metadata["low_coverage_pages"] == [1]


def test_retries_wait_for_all_blocks_and_run_at_most_two_pages(tmp_path):
    from threading import Lock
    from time import sleep

    lock = Lock()
    state = {"initial_completed": 0, "active": 0, "peak": 0, "pages": {}}

    class PhasedReader(ChunkProvider):
        def extract_supplier_offer(self, **kwargs):
            context = kwargs["page_context"]
            is_retry = context.page_count == 1
            with lock:
                if is_retry:
                    assert state["initial_completed"] == 10
                    state["pages"][context.start_page] = (
                        state["pages"].get(context.start_page, 0) + 1
                    )
                    assert len(state["pages"]) <= 2
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
            try:
                sleep(0.003)
                extracted = super().extract_supplier_offer(**kwargs)
                if not is_retry:
                    for page in extracted.page_extractions:
                        page.coverage.estimated_offer_blocks = 100
                return extracted
            finally:
                with lock:
                    state["active"] -= 1
                    if not is_retry:
                        state["initial_completed"] += 1
                    else:
                        state["pages"][context.start_page] -= 1
                        if not state["pages"][context.start_page]:
                            del state["pages"][context.start_page]

    a, b = PhasedReader(), PhasedReader()
    extracted = DualDocumentExtractionProvider(
        [("gemini", a), ("openai", b)]
    ).extract_supplier_offer(document_path=make_pdf(tmp_path, 27))
    assert extracted.document_metadata["retried_pages"] == list(range(1, 28))
    assert extracted.document_metadata["retry_provider_calls"] == 54
    assert state["peak"] <= 4
    assert all(
        a.calls.count((page, page)) == b.calls.count((page, page)) == 1 for page in range(1, 28)
    )


def test_empty_reread_does_not_erase_previously_recovered_offers(tmp_path):
    class EmptyReread(ChunkProvider):
        def extract_supplier_offer(self, **kwargs):
            result = super().extract_supplier_offer(**kwargs)
            if kwargs["page_context"].page_count == 1:
                result.items = []
                for page in result.page_extractions:
                    page.items = []
            return result

    extracted = DualDocumentExtractionProvider(
        [
            ("gemini", EmptyReread(low_page=1)),
            ("openai", EmptyReread(low_page=1)),
        ]
    ).extract_supplier_offer(document_path=make_pdf(tmp_path, 2))
    assert len(extracted.items) == 2
    assert extracted.document_metadata["retried_pages"] == [1]
    assert extracted.document_metadata["needs_review_pages"] == [1]
    assert extracted.document_metadata["agreed_item_count"] == 2


def test_retry_conflict_keeps_initial_price_but_requires_human_review(tmp_path):
    class ChangedPrice(ChunkProvider):
        def extract_supplier_offer(self, **kwargs):
            extracted = super().extract_supplier_offer(**kwargs)
            if kwargs["page_context"].page_count == 1:
                extracted.items[0].offer_price = Decimal("999")
            return extracted

    extracted = DualDocumentExtractionProvider(
        [
            ("gemini", ChunkProvider(low_page=1)),
            ("openai", ChangedPrice(low_page=1)),
        ]
    ).extract_supplier_offer(document_path=make_pdf(tmp_path, 2))
    initial_offer = next(item for item in extracted.items if item.raw_name == "Producto Pagina 1")
    assert initial_offer.offer_price == 100
    assert initial_offer.resolved_by == "human_review"
    assert extracted.document_metadata["needs_review_pages"] == [1]


def test_missing_page_report_triggers_one_reread(tmp_path):
    class MissingReport(ChunkProvider):
        def extract_supplier_offer(self, **kwargs):
            extracted = super().extract_supplier_offer(**kwargs)
            if kwargs["page_context"].page_count > 1:
                extracted.page_extractions.pop()
            return extracted

    extracted = DualDocumentExtractionProvider(
        [
            ("gemini", MissingReport()),
            ("openai", ChunkProvider()),
        ]
    ).extract_supplier_offer(document_path=make_pdf(tmp_path, 2))
    assert extracted.document_metadata["retried_pages"] == [2]
    assert extracted.document_metadata["retry_reasons"]["2"] == ["missing_page_report"]
