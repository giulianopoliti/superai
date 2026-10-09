from decimal import Decimal
from io import BytesIO

import pytest
from pypdf import PdfWriter

from app.modules.procurement.analysis_contracts import (
    AnalysisMethod,
    BlockInterpretationResult,
    DocumentReadResult,
    EvidenceRef,
    OfferInterpretation,
    ProviderCallRecord,
    ReaderBlocksExtractionV2,
    ReaderStructuredExtractionV2,
    TokenUsage,
)
from app.modules.procurement.document_analysis import (
    AnalysisBudget,
    PageRecoveryRegistry,
    SupplierOfferDocumentAnalysisService,
)


class Reader:
    def __init__(self, *, visual=False, repeats=1, empty_retry=False):
        self.requests = []
        self.visual, self.repeats, self.empty_retry = visual, repeats, empty_retry

    def read(self, *, request):
        self.requests.append(request)
        blocks = [
            {
                "fragments": [{"text": "Aceite 900 ml $1200 por unidad", "legibility": "readable"}],
                "page_condition_indices": [],
                "warnings": [],
            }
            for _ in range(self.repeats)
        ]
        if self.empty_retry and request.phase == "reread":
            blocks = []
        cls = ReaderBlocksExtractionV2
        if request.analysis_method == AnalysisMethod.OPENAI_STRUCTURED:
            cls = ReaderStructuredExtractionV2
            for block in blocks:
                block["proposals"] = []
        extraction = cls(
            supplier_name="Proveedor",
            warnings=[],
            pages=[
                {
                    "page_number": n,
                    "page_conditions": [],
                    "blocks": blocks,
                    "coverage": {
                        "estimated_offer_blocks": self.repeats,
                        "needs_visual_review": self.visual and request.phase == "initial",
                        "warnings": [],
                    },
                }
                for n in range(1, request.end_page - request.start_page + 2)
            ],
        )
        return DocumentReadResult(
            extraction=extraction,
            call=ProviderCallRecord(
                call_id=request.reading_id,
                run_id=request.run_id,
                reading_id=request.reading_id,
                provider="openai",
                model="fake",
                prompt_version="fake",
                schema_version="v2",
                analysis_method=request.analysis_method,
                phase=request.phase,
                status="success",
                usage=TokenUsage(
                    input_tokens=100,
                    cached_input_tokens=50,
                    output_tokens=10,
                    reasoning_output_tokens=2,
                    total_tokens=110,
                ),
            ),
        )


class Assembler:
    def assemble_interpretation(self, *, business_id, block, classification, candidates):
        return BlockInterpretationResult(
            block_id=block.block_id,
            offers=[
                OfferInterpretation(
                    offer_id=block.block_id + ":offer",
                    block_id=block.block_id,
                    business_id=business_id,
                    raw_name="Aceite 900 ml",
                    offer_price=Decimal("1200"),
                    price_type="unit",
                    price_status="confirmed",
                    field_evidence={
                        "raw_name": [
                            EvidenceRef(
                                fragment_id=block.fragments[0].fragment_id, quote="Aceite 900 ml"
                            )
                        ],
                        "offer_price": [
                            EvidenceRef(fragment_id=block.fragments[0].fragment_id, quote="$1200")
                        ],
                        "price_type": [
                            EvidenceRef(
                                fragment_id=block.fragments[0].fragment_id, quote="por unidad"
                            )
                        ],
                    },
                )
            ],
        )


def pdf(tmp_path, pages=1):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    data = BytesIO()
    writer.write(data)
    path = tmp_path / "offers.pdf"
    path.write_bytes(data.getvalue())
    return path


def test_recovery_is_shared_one_per_page_and_retains_versions(tmp_path):
    reader = Reader(visual=True)
    result = SupplierOfferDocumentAnalysisService(reader=reader, assembler=Assembler()).analyze(
        business_id="tenant",
        document_path=pdf(tmp_path, 2),
        method="openai_structured",
    )
    assert [r.phase for r in reader.requests] == ["initial", "initial", "reread", "reread"]
    ledger = result.document_metadata["analysis_ledger"]
    assert len(ledger["blocks"]) == 4  # two originals, two versions
    assert all(p["reread_count"] == 1 for p in ledger["pages"])
    assert len(result.items) == 2
    assert result.document_metadata["analysis"]["observed_block_count"] == 2
    assert result.document_metadata["analysis"]["usage"]["total_tokens"] == 440
    assert result.document_metadata["analysis"]["usage"]["input_tokens"] == 400


def test_repeated_offers_not_deduplicated_by_name(tmp_path):
    result = SupplierOfferDocumentAnalysisService(
        reader=Reader(repeats=2), assembler=Assembler()
    ).analyze(
        business_id="tenant",
        document_path=pdf(tmp_path),
        method="openai_structured",
    )
    assert len(result.items) == 2
    ids = [i.provider_observations["analysis_v2"]["offer_id"] for i in result.items]
    assert len(set(ids)) == 2


def test_empty_reread_keeps_originals_and_pending_coverage(tmp_path):
    result = SupplierOfferDocumentAnalysisService(
        reader=Reader(visual=True, empty_retry=True),
        assembler=Assembler(),
    ).analyze(business_id="tenant", document_path=pdf(tmp_path), method="openai_structured")
    assert len(result.items) == 1
    assert result.document_metadata["analysis"]["status"] == "partial"
    assert result.document_metadata["analysis"]["review_page_numbers"] == [1]


def test_same_text_reread_without_price_never_supersedes_prior_offer(tmp_path):
    reader = Reader(visual=True)

    class MissingPrice(Assembler):
        def assemble_interpretation(self, **kwargs):
            result = super().assemble_interpretation(**kwargs)
            reading_id = kwargs["block"].reading_id
            if next(r for r in reader.requests if r.reading_id == reading_id).phase == "reread":
                result.offers[0].offer_price = None
            return result

    result = SupplierOfferDocumentAnalysisService(reader=reader, assembler=MissingPrice()).analyze(
        business_id="tenant",
        document_path=pdf(tmp_path),
        method="openai_structured",
    )
    assert [i.offer_price for i in result.items] == [Decimal("1200")]
    assert result.document_metadata["analysis"]["status"] == "partial"
    assert result.document_metadata["analysis_ledger"]["pending_blocks"]


@pytest.mark.parametrize("regression", ["variant", "price_status", "identity_status"])
def test_recovery_cannot_drop_variant_or_degrade_confirmed_decisions(tmp_path, regression):
    reader = Reader(visual=True)

    class Regression(Assembler):
        def assemble_interpretation(self, **kwargs):
            result = super().assemble_interpretation(**kwargs)
            offer = result.offers[0]
            offer.identity_status, offer.product_id = "confirmed", "product"
            phase = next(
                r.phase for r in reader.requests if r.reading_id == kwargs["block"].reading_id
            )
            if phase == "initial" and regression == "variant":
                other = offer.model_copy(deep=True)
                other.offer_id += "-variant"
                other.raw_name, other.offer_price = "Aceite variante B", Decimal("2000")
                result.offers.append(other)
            elif phase == "reread" and regression == "price_status":
                offer.price_status = "pending"
            elif phase == "reread" and regression == "identity_status":
                offer.identity_status, offer.product_id = "pending", None
            return result

    result = SupplierOfferDocumentAnalysisService(reader=reader, assembler=Regression()).analyze(
        business_id="tenant",
        document_path=pdf(tmp_path),
        method="openai_structured",
    )
    assert len(result.items) == (2 if regression == "variant" else 1)
    assert result.items[0].price_type.value == "unit"
    assert result.document_metadata["analysis"]["status"] == "partial"
    pending = result.document_metadata["analysis_ledger"]["pending_blocks"]
    assert any("reread_quality_regression" in p["reason_codes"] for p in pending)


def test_two_initial_readers_overlap_and_results_map_once(tmp_path):
    from threading import Barrier, Lock

    reader = Reader()
    barrier, lock = Barrier(2), Lock()
    active, peak = 0, 0
    original = reader.read

    def read(*, request):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        barrier.wait(timeout=5)
        try:
            return original(request=request)
        finally:
            with lock:
                active -= 1

    reader.read = read
    result = SupplierOfferDocumentAnalysisService(reader=reader, assembler=Assembler()).analyze(
        business_id="tenant",
        document_path=pdf(tmp_path, 4),
        method="openai_structured",
    )
    assert peak == 2
    assert len(reader.requests) == 4
    assert [i.page_number for i in result.items] == [1, 2, 3, 4]
    assert len(result.document_metadata["analysis_ledger"]["calls"]) == 4


def test_budget_keeps_valid_partial_result_without_second_visual_route(tmp_path):
    reader = Reader(visual=True)
    result = SupplierOfferDocumentAnalysisService(
        reader=reader, assembler=Assembler(), max_calls=1
    ).analyze(
        business_id="tenant",
        document_path=pdf(tmp_path),
        method="openai_structured",
    )
    assert len(reader.requests) == 1
    assert len(result.items) == 1
    assert result.document_metadata["analysis"]["status"] == "partial"


def test_b_jev_failure_keeps_blocks_and_never_falls_back_or_rereads(tmp_path):
    class FailingClassifier:
        def classify_block(self, *, request):
            raise TimeoutError()

    reader = Reader()
    result = SupplierOfferDocumentAnalysisService(
        reader=reader,
        assembler=Assembler(),
        classifier=FailingClassifier(),
        question_builder=lambda **kwargs: [],
        value_detector=lambda **kwargs: [],
    ).analyze(business_id="tenant", document_path=pdf(tmp_path), method="openai_blocks_jev")
    assert len(reader.requests) == 1
    assert reader.requests[0].analysis_method == AnalysisMethod.OPENAI_BLOCKS_JEV
    assert not result.items
    ledger = result.document_metadata["analysis_ledger"]
    assert ledger["pending_blocks"][0]["text"].startswith("Aceite")
    assert "jev_classification_failed" in ledger["pending_blocks"][0]["reason_codes"]
    assert len(ledger["calls"]) == 2
    assert ledger["calls"][1]["phase"] == "classification"
    assert ledger["calls"][1]["usage"] is None


def test_registry_reserves_once_and_usage_aggregates_once():
    registry, budget = PageRecoveryRegistry(), AnalysisBudget(max_calls=10)
    assert registry.reserve(1, budget)
    assert not registry.reserve(1, budget)
    call = ProviderCallRecord(
        call_id="c",
        run_id="r",
        reading_id="read",
        provider="fake",
        model="fake",
        prompt_version="fake",
        schema_version="fake",
        analysis_method="openai_structured",
        phase="initial",
        status="success",
        usage=TokenUsage(total_tokens=123),
    )
    budget.record(call)
    budget.record(call)
    assert budget.usage().total_tokens == 123
    assert budget.usage().input_tokens is None


def test_policy_questions_prepared_and_failed_decision_audited_in_one_batch(tmp_path):
    from app.modules.procurement.analysis_contracts import IdentityCandidate
    from app.modules.procurement.decision_policy import DocumentDecisionPolicy

    batches, requests = [], []

    def retrieve(**kwargs):
        batches.append(kwargs)
        return {
            o.offer_id: [
                IdentityCandidate(
                    candidate_id="candidate",
                    product_id="product",
                    business_id="tenant",
                    name="Aceite 900 ml",
                )
            ]
            for o in kwargs["offers"]
        }

    class Decisions:
        def decide_offer(self, *, request):
            requests.append(request)
            raise TimeoutError()

    result = SupplierOfferDocumentAnalysisService(
        reader=Reader(repeats=2),
        assembler=Assembler(),
        policy=DocumentDecisionPolicy(),
        candidate_retriever=retrieve,
        decision_provider=Decisions(),
    ).analyze(business_id="tenant", document_path=pdf(tmp_path), method="openai_structured")
    assert len(batches) == 1
    assert len(batches[0]["offers"]) == 2
    assert batches[0]["internal_top_k"] == 5
    assert all(r.questions for r in requests)
    assert len(requests) == 2
    assert len(result.document_metadata["analysis_ledger"]["calls"]) == 3
    assert result.document_metadata["analysis"]["status"] == "partial"


def test_foreign_classifier_call_is_not_recorded_as_our_usage(tmp_path):
    from app.modules.procurement.analysis_contracts import BlockClassificationResult

    class Classifier:
        def classify_block(self, *, request):
            return BlockClassificationResult(
                run_id=request.block.run_id,
                block_id=request.block.block_id,
                calls=[
                    ProviderCallRecord(
                        call_id="foreign",
                        run_id="another-run",
                        reading_id=request.block.reading_id,
                        provider="jev",
                        model="fake",
                        prompt_version="fake",
                        schema_version="v1",
                        analysis_method="openai_blocks_jev",
                        phase="classification",
                        status="success",
                        usage=TokenUsage(total_tokens=999999),
                    )
                ],
            )

    result = SupplierOfferDocumentAnalysisService(
        reader=Reader(),
        assembler=Assembler(),
        classifier=Classifier(),
        question_builder=lambda **kwargs: [],
        value_detector=lambda **kwargs: [],
    ).analyze(business_id="tenant", document_path=pdf(tmp_path), method="openai_blocks_jev")
    calls = result.document_metadata["analysis_ledger"]["calls"]
    assert not any(c["call_id"] == "foreign" for c in calls)
    assert result.document_metadata["analysis"]["usage"] is None
    assert result.document_metadata["analysis"]["status"] == "partial"


def test_phase_quotas_protect_reading_and_decision_calls():
    budget = AnalysisBudget(
        max_calls=6, phase_limits={"reading": 3, "classification": 2, "decision": 1}
    )
    assert budget.reserve("classification")
    assert budget.reserve("classification")
    assert not budget.reserve("classification")
    assert budget.reason == "classification_call_budget_exhausted"
    assert all(budget.reserve("reading") for _ in range(3))
    assert budget.reserve("decision")
    assert budget.reserved == 6


def test_local_preflight_rejections_do_not_reserve_or_record_http(tmp_path):
    from app.modules.procurement.analysis_contracts import BlockClassificationResult

    class LocalRejection:
        def preflight(self, *, request):
            return BlockClassificationResult(
                run_id=request.block.run_id,
                block_id=request.block.block_id,
                reason_codes=["jev_request_too_large"],
            )

        def classify_block(self, *, request):
            raise AssertionError("Preflight rejection must never reach transport")

    reader = Reader()
    result = SupplierOfferDocumentAnalysisService(
        reader=reader,
        assembler=Assembler(),
        classifier=LocalRejection(),
        max_calls=10,
        question_builder=lambda **kwargs: [],
        value_detector=lambda **kwargs: [],
    ).analyze(business_id="tenant", document_path=pdf(tmp_path, 6), method="openai_blocks_jev")
    assert len(reader.requests) == 3
    summary = result.document_metadata["analysis"]
    assert summary["read_page_count"] == summary["attempted_page_count"] == 6
    assert summary["provider_call_count"] == 3
    assert summary["local_rejection_count"] == 6
    assert result.document_metadata["analysis_execution"]["phase_reserved_calls"] == {"reading": 3}
    assert all(
        not a["call_ids"] for a in result.document_metadata["analysis_ledger"]["decision_audit"]
    )


def test_partitioned_provider_reserves_only_actual_post_calls(tmp_path):
    from app.modules.procurement.analysis_contracts import BlockClassificationResult

    class Partitioned:
        supports_call_reservation = True

        def preflight(self, *, request):
            return None

        def classify_block(self, *, request, reserve_call):
            calls, reasons = [], []
            for index in range(3):
                if not reserve_call():
                    reasons.append("jev_call_budget_exceeded")
                    break
                calls.append(
                    ProviderCallRecord(
                        call_id=f"{request.block.block_id}:{index}",
                        run_id=request.block.run_id,
                        reading_id=request.block.reading_id,
                        provider="jev",
                        model="fake",
                        prompt_version="fake",
                        schema_version="v2",
                        analysis_method="openai_blocks_jev",
                        phase="classification",
                        status="success",
                    )
                )
            return BlockClassificationResult(
                run_id=request.block.run_id,
                block_id=request.block.block_id,
                calls=calls,
                reason_codes=reasons,
            )

    result = SupplierOfferDocumentAnalysisService(
        reader=Reader(),
        assembler=Assembler(),
        classifier=Partitioned(),
        max_calls=10,
        question_builder=lambda **kwargs: [],
        value_detector=lambda **kwargs: [],
    ).analyze(business_id="tenant", document_path=pdf(tmp_path, 4), method="openai_blocks_jev")
    execution = result.document_metadata["analysis_execution"]
    assert execution["phase_reserved_calls"] == {"reading": 2, "classification": 4}
    assert result.document_metadata["analysis"]["provider_call_count"] == 6
    assert result.document_metadata["analysis"]["local_rejection_count"] == 0
    assert (
        len(
            [
                c
                for c in result.document_metadata["analysis_ledger"]["calls"]
                if c["provider"] == "jev"
            ]
        )
        == 4
    )


def test_jev_has_one_slot_and_does_not_stop_two_readers(tmp_path):
    from threading import Barrier, Event, Lock, get_ident

    from app.modules.procurement.analysis_contracts import BlockClassificationResult

    lock, initial = Lock(), Barrier(2)
    semantic_started, second_finished = Event(), Event()
    main_thread = get_ident()
    active = {"reading": 0, "semantic": 0}
    peak = dict(active)

    class ConcurrentReader(Reader):
        def read(self, *, request):
            assert get_ident() != main_thread
            with lock:
                active["reading"] += 1
                peak["reading"] = max(peak["reading"], active["reading"])
            try:
                if request.start_page <= 3:
                    initial.wait(timeout=5)
                    if request.start_page == 3:
                        assert semantic_started.wait(timeout=5)
                        second_finished.set()
                else:
                    initial.wait(timeout=5)
                assert request.document_path.exists()
                return super().read(request=request)
            finally:
                with lock:
                    active["reading"] -= 1

    class SlowClassifier:
        def classify_block(self, *, request):
            assert get_ident() != main_thread
            with lock:
                active["semantic"] += 1
                peak["semantic"] = max(peak["semantic"], active["semantic"])
            try:
                semantic_started.set()
                assert second_finished.wait(timeout=5)
                return BlockClassificationResult(
                    run_id=request.block.run_id, block_id=request.block.block_id
                )
            finally:
                with lock:
                    active["semantic"] -= 1

    class MainThreadAssembler(Assembler):
        def assemble_interpretation(self, **kwargs):
            assert get_ident() == main_thread
            return super().assemble_interpretation(**kwargs)

    reader = ConcurrentReader()
    result = SupplierOfferDocumentAnalysisService(
        reader=reader,
        assembler=MainThreadAssembler(),
        classifier=SlowClassifier(),
        question_builder=lambda **kwargs: [],
        value_detector=lambda **kwargs: [],
    ).analyze(business_id="tenant", document_path=pdf(tmp_path, 8), method="openai_blocks_jev")
    assert len(result.items) == 8
    assert peak == {"reading": 2, "semantic": 1}
    assert not any(r.document_path.exists() for r in reader.requests)


@pytest.mark.parametrize("method", ["openai_structured", "openai_blocks_jev"])
@pytest.mark.parametrize("elapsed", [150.0, 200.0])
def test_late_active_reader_keeps_pdf_and_backend_sends_all_later_batches(
    tmp_path, caplog, method, elapsed
):
    from threading import Barrier, Lock

    moment, lock, barrier = [0.0], Lock(), Barrier(2)

    class LateReader(Reader):
        def read(self, *, request):
            barrier.wait(timeout=5)
            with lock:
                moment[0] += elapsed
            assert request.document_path.exists()
            result = super().read(request=request)
            assert request.document_path.exists()
            return result

    reader = LateReader()
    caplog.set_level("INFO", logger="uvicorn.error")
    service = SupplierOfferDocumentAnalysisService(
        reader=reader,
        assembler=Assembler(),
        clock=lambda: moment[0],
        timeout_seconds=180,
        question_builder=lambda **kwargs: [],
        value_detector=lambda **kwargs: [],
    )
    if method == "openai_blocks_jev":
        from types import SimpleNamespace

        from app.modules.procurement.analysis_contracts import BlockClassificationResult

        service.classifier = SimpleNamespace(
            classify_block=lambda request: BlockClassificationResult(
                run_id=request.block.run_id, block_id=request.block.block_id
            )
        )
    page_count = 24
    result = service.analyze(
        business_id="tenant", document_path=pdf(tmp_path, page_count), method=method
    )
    summary = result.document_metadata["analysis"]
    expected_requests = 24 if method == "openai_structured" else 12
    assert len(reader.requests) == expected_requests
    assert summary["read_page_count"] == summary["attempted_page_count"] == 24
    assert summary["unattempted_page_numbers"] == []
    assert summary["failed_page_numbers"] == []
    assert summary["duration_seconds"] == elapsed * expected_requests
    assert result.document_metadata["recovery_pending"]["page_numbers"] == []
    assert result.document_metadata["recovery_pending"]["continuation_available"] is False
    assert not any(r.document_path.exists() for r in reader.requests)
    import json

    events = [
        json.loads(record.getMessage().removeprefix("document_analysis "))
        for record in caplog.records
        if record.getMessage().startswith("document_analysis ")
    ]
    assert not any(event["event"] == "reading_stopped" for event in events)
    batches = [event for event in events if event["event"] == "batch_started"]
    assert len(batches) == expected_requests // 2
    assert batches[-1]["end_page"] == 24
    execution = result.document_metadata["analysis_execution"]
    assert execution["budget_scope"] == "batch"
    assert execution["phase_reserved_calls"]["reading"] == expected_requests
    assert summary["usage"]["total_tokens"] is not None
    assert len(result.document_metadata["analysis_ledger"]["calls"]) >= expected_requests
    assert events[-1]["event"] == "finished"
    assert events[-1]["unattempted_pages"] == []
    assert "Aceite" not in caplog.text


@pytest.mark.parametrize("method", ["openai_structured", "openai_blocks_jev"])
@pytest.mark.parametrize("workers", [1, 2])
def test_early_timeouts_do_not_block_later_pages_or_erase_prior_call_metrics(
    tmp_path, method, workers
):
    from types import SimpleNamespace

    from app.modules.procurement.analysis_contracts import BlockClassificationResult

    moment = [0.0]

    class FailingFirstReader(Reader):
        def read(self, *, request):
            result = super().read(request=request)
            if request.start_page == 1:
                moment[0] += 200
                result.extraction = None
                result.call.status = "error"
                result.call.safe_error = "openai_read_failed:APITimeoutError"
                result.call.usage = None
            return result

    reader = FailingFirstReader()
    # A single call quota intentionally makes each batch one reader request.
    result = SupplierOfferDocumentAnalysisService(
        reader=reader, assembler=Assembler(), chunk_workers=workers, max_calls=1,
        clock=lambda: moment[0],
        classifier=SimpleNamespace(classify_block=lambda request: BlockClassificationResult(
            run_id=request.block.run_id, block_id=request.block.block_id
        )),
        question_builder=lambda **kwargs: [], value_detector=lambda **kwargs: [],
    ).analyze(business_id="tenant", document_path=pdf(tmp_path, 24), method=method)
    summary = result.document_metadata["analysis"]
    assert summary["attempted_page_count"] == 24
    assert summary["unattempted_page_numbers"] == []
    assert summary["failed_page_numbers"] == ([1] if method == "openai_structured" else [1, 2])
    assert summary["status"] == "partial"
    assert summary["usage"] is None  # Preserve unknown usage of first failed call.
    execution = result.document_metadata["analysis_execution"]
    assert execution["batch_count"] == len(reader.requests)
    assert execution["phase_reserved_calls"]["reading"] == len(reader.requests)
    assert execution["batches"][-1]["end_page"] == 24
    assert all(not request.document_path.exists() for request in reader.requests)


def test_initial_batch_reservation_survives_elapsed_time_but_respects_call_limit():
    budget = AnalysisBudget(max_calls=1, timeout_seconds=1, clock=lambda: 0)
    budget.clock = lambda: 200
    assert not budget.reserve("reading")
    assert budget.reserve("reading", enforce_deadline=False)
    assert not budget.reserve("reading", enforce_deadline=False)
    assert budget.reason == "call_budget_exhausted"
