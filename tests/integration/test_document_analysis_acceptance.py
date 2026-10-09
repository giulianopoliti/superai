"""Independent offline acceptance at the available contract/service boundaries.

Fake reading/assembly proves orchestration and preservation, not visual fidelity,
real fragment interpretation, paid API quality, frontend, or DB persistence.
"""

import json
import re
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from pypdf import PdfWriter
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db.models import Base
from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.modules.procurement.analysis_contracts import (
    AnalysisLedger,
    AnalysisMethod,
    BlockClassificationResult,
    BlockInterpretationResult,
    DocumentReadResult,
    EvidenceFragment,
    EvidenceRef,
    IdentityCandidate,
    MinimumPurchase,
    OfferDecisionRequest,
    OfferInterpretation,
    OfferTextBlock,
    ProviderCallRecord,
    ReaderBlocksExtractionV2,
    ReaderStructuredExtractionV2,
)
from app.modules.procurement.block_interpretation import (
    DeterministicBlockAssembler,
    detect_value_candidates,
)
from app.modules.procurement.decision_policy import DocumentDecisionPolicy, comparable_unit_price
from app.modules.procurement.document_analysis import SupplierOfferDocumentAnalysisService
from app.modules.procurement.normalization import normalize_text
from app.modules.procurement.pricing import normalize_offer_price
from app.modules.procurement.schemas import Product
from app.providers import jev_analysis
from app.providers.documents.openai import OpenAISupplierOfferDocumentProvider
from app.providers.jev_analysis import JevAnalysisClient, JevAnalysisProvider, prepare_block_request
from app.settings import settings
from scripts.benchmark_procurement import analyze

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "procurement_analysis"
CORPUS = json.loads((FIXTURES / "cases.json").read_text(encoding="utf-8"))
CASES = {row["case_id"]: row for row in CORPUS["cases"]}


@pytest.fixture(autouse=True)
def isolate_qa_from_live_settings(monkeypatch):
    monkeypatch.setattr(settings, "document_analysis_v2_enabled", False)
    monkeypatch.setattr(settings, "openai_api_key", None)
    monkeypatch.setattr(settings, "typesafe_api_key", None)
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(main, "SessionLocal", None)
    monkeypatch.setattr(
        jev_analysis,
        "_http_transport",
        lambda *a, **kw: pytest.fail("QA requires an explicit fake HTTP transport"),
    )


class FakeReader:
    def __init__(self, case, *, visual_gap=False):
        self.case, self.visual_gap, self.requests = case, visual_gap, []

    def read(self, *, request):
        self.requests.append(request)
        method_a = request.analysis_method == AnalysisMethod.OPENAI_STRUCTURED
        fragments = [{"text": text, "legibility": "readable"} for text in self.case["fragments"]]
        block = {"fragments": fragments, "page_condition_indices": [], "warnings": []}
        if method_a:
            block["proposals"] = []
        wire = ReaderStructuredExtractionV2 if method_a else ReaderBlocksExtractionV2
        blocks = [block] if fragments else []
        if self.case["case_id"] == "repeated_offers":
            blocks = [{**block, "fragments": [fragment]} for fragment in fragments]
        extraction = wire.model_validate(
            {
                "supplier_name": "Proveedor sintético",
                "warnings": [],
                "pages": [
                    {
                        "page_number": 1,
                        "page_conditions": [],
                        "blocks": blocks,
                        "coverage": {
                            "estimated_offer_blocks": 1 if fragments else 0,
                            "needs_visual_review": self.visual_gap,
                            "warnings": [],
                        },
                    }
                ],
            }
        )
        call = ProviderCallRecord(
            call_id=f"fake-{request.reading_id}",
            run_id=request.run_id,
            reading_id=request.reading_id,
            provider="fake-openai",
            model="fake",
            prompt_version="synthetic-v1",
            schema_version="supplier_offer_reader_v2",
            analysis_method=request.analysis_method,
            phase=request.phase,
            status="success",
        )
        return DocumentReadResult(extraction=extraction, call=call)


class FakeClassifier:
    def __init__(self, *, fail=False):
        self.fail, self.requests = fail, []

    def classify_block(self, *, request):
        self.requests.append(request)
        if self.fail:
            raise TimeoutError("synthetic failure")
        return BlockClassificationResult(
            run_id=request.block.run_id, block_id=request.block.block_id
        )


class FakeAssembler:
    def __init__(self, *, price=None, foreign_tenant=False):
        self.price, self.foreign_tenant = price, foreign_tenant

    def assemble_interpretation(self, *, business_id, block, classification, candidates):
        offers = (
            []
            if self.price is None
            else [
                OfferInterpretation(
                    offer_id=f"{block.block_id}:offer",
                    block_id=block.block_id,
                    business_id="foreign-tenant" if self.foreign_tenant else business_id,
                    raw_name="Artículo sintético",
                    offer_price=Decimal(self.price),
                    price_type="unit",
                    price_status="confirmed",
                )
            ]
        )
        return BlockInterpretationResult(block_id=block.block_id, offers=offers)


def service_for(case_id, **options):
    reader = FakeReader(CASES[case_id], visual_gap=options.pop("visual_gap", False))
    classifier = FakeClassifier(fail=options.pop("fail_jev", False))
    service = SupplierOfferDocumentAnalysisService(
        reader=reader,
        classifier=classifier,
        assembler=FakeAssembler(**options),
        value_detector=lambda **kwargs: [],
        question_builder=lambda **kwargs: [],
    )
    return service, reader, classifier


def run(service, method):
    return service.analyze(
        business_id="synthetic-tenant",
        document_path=Path("fake.png"),
        method=method,
        supplier_hint="Proveedor sintético",
    )


@pytest.mark.parametrize("method", list(AnalysisMethod))
@pytest.mark.parametrize("case_id", ["missing_price", "discount_without_amount", "illegible"])
def test_unpriced_blocks_preserve_literal_evidence_and_method(case_id, method):
    service, reader, _ = service_for(case_id)
    result = run(service, method)
    ledger = AnalysisLedger.model_validate(result.document_metadata["analysis_ledger"])
    assert result.items == []
    assert ledger.blocks[0].full_text == "\n".join(CASES[case_id]["fragments"])
    assert ledger.pending_blocks[0].text == ledger.blocks[0].full_text
    assert result.document_metadata["analysis"]["method"] == method
    assert result.document_metadata["analysis"]["status"] == "partial"
    assert len(reader.requests) == 1  # semantic absence alone does not trigger vision


@pytest.mark.parametrize("method", list(AnalysisMethod))
def test_declared_empty_page_is_not_reread(method):
    service, reader, _ = service_for("empty_page")
    result = run(service, method)
    summary = result.document_metadata["analysis"]
    assert summary["observed_block_count"] == 0
    assert summary["page_count"] == 1
    assert len(reader.requests) == 1


def test_b_classifier_failure_keeps_evidence_without_switching_to_a():
    service, reader, classifier = service_for("jev_failure", fail_jev=True)
    result = run(service, AnalysisMethod.OPENAI_BLOCKS_JEV)
    assert len(reader.requests) == len(classifier.requests) == 1
    assert all(r.analysis_method == AnalysisMethod.OPENAI_BLOCKS_JEV for r in reader.requests)
    assert result.document_metadata["analysis"]["status"] == "partial"
    assert (
        "jev_classification_failed"
        in result.document_metadata["analysis_ledger"]["blocks"][0]["reason_codes"]
    )
    assert result.document_metadata["analysis_ledger"]["pending_blocks"][0]["text"]
    calls = result.document_metadata["analysis_ledger"]["calls"]
    [failure] = [call for call in calls if call["phase"] == "classification"]
    assert failure["status"] == "error"
    assert failure["usage"] is None
    assert failure["run_id"] == result.document_metadata["analysis"]["run_id"]
    assert failure["reading_id"] == calls[0]["reading_id"]


@pytest.mark.parametrize("method", list(AnalysisMethod))
def test_shared_recovery_is_one_reread_and_versions_survive(method, tmp_path):
    service, reader, _ = service_for("one_reread", price="1500", visual_gap=True)
    result = run(service, method)
    ledger = result.document_metadata["analysis_ledger"]
    assert [request.phase for request in reader.requests] == ["initial", "reread"]
    assert ledger["pages"][0]["reread_count"] == 1
    assert len(ledger["blocks"]) == 2
    assert len(result.items) == 1
    assert result.document_metadata["analysis"]["observed_block_count"] == 1
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps({"metadata": result.document_metadata}), encoding="utf-8")
    [report] = analyze([path], provenance="synthetic")["runs"]
    assert report["blocks"]["current"] == 1
    assert report["blocks"]["historical"] == 1
    assert report["usage"]["input_tokens"]["known_sum"] is None


def test_foreign_assembler_output_is_not_published():
    service, _, _ = service_for("tenant_isolation", price="1500", foreign_tenant=True)
    result = run(service, AnalysisMethod.OPENAI_STRUCTURED)
    assert result.items == []
    assert result.document_metadata["analysis"]["status"] == "partial"
    assert (
        "interpretation_failed"
        in result.document_metadata["analysis_ledger"]["blocks"][0]["reason_codes"]
    )


@pytest.mark.parametrize(
    "case_id", ["pack_x6", "contents_x48", "minimum_two_packs", "unknown_quantity"]
)
def test_existing_price_calculation_uses_commercial_quantity_only(case_id):
    case = CASES[case_id]
    data = {**case["price_input"], "offer_price": Decimal(case["price_input"]["offer_price"])}
    actual = normalize_offer_price(**data)
    expected = case["expected"]["unit_price"]
    assert actual == (Decimal(expected) if expected is not None else None)


def test_minimum_purchase_roundtrip_keeps_value_unit_scope_and_reference():
    expected = CASES["minimum_two_packs"]["expected"]["minimum_purchase"]
    minimum = MinimumPurchase(**expected, refs=[EvidenceRef(fragment_id="f", quote="2 bultos")])
    decoded = MinimumPurchase.model_validate_json(minimum.model_dump_json())
    assert (decoded.value, decoded.unit, decoded.scope) == (Decimal("2"), "pack", "offer")
    assert decoded.refs[0].quote == "2 bultos"


def test_foreign_condition_reference_is_rejected_by_shared_contract():
    fragment = EvidenceFragment(fragment_id="f", text="Arroz $1500", order=0, legibility="readable")
    with pytest.raises(ValidationError):
        OfferTextBlock(
            block_id="b",
            run_id="r",
            reading_id="read",
            page_number=1,
            order_on_page=0,
            full_text=fragment.text,
            fragments=[fragment],
            condition_refs=[CASES["foreign_reference"]["fault"]["selected_reference"]],
        )


@pytest.mark.parametrize("fault", ["jev_partial", "invalid_probabilities", "jev_failure"])
def test_textual_jev_faults_never_become_valid_price_answers(fault):
    def transport(request, timeout, max_bytes):
        if fault == "jev_failure":
            raise TimeoutError("synthetic")
        probabilities = {"unit": 1, "pack": 0, "unknown": 0}
        if fault == "invalid_probabilities":
            probabilities.update(CASES[fault]["fault"]["probabilities"])
        answers = (
            {}
            if fault == "jev_partial"
            else {
                "basis": {
                    "type": "choice",
                    "choice": "unit",
                    "confidence": 0.99,
                    "probabilities": probabilities,
                }
            }
        )
        return json.dumps({"model": "jev-1.13.0", "answers": answers}).encode()

    client = JevAnalysisClient(api_key="synthetic-key", transport=transport)
    result = client.evaluate(
        state={"offer": "ARROZ EJEMPLO $1500"},
        questions={
            "basis": {
                "type": "choice",
                "instructions": "Choose basis from this offer only.",
                "criteria": {"unit": "Unit", "pack": "Pack", "unknown": "Unclear"},
            }
        },
    )
    assert "basis" not in result.answers
    assert result.errors or result.error
    assert client.call_count == 1


@pytest.mark.parametrize("method", list(AnalysisMethod))
def test_identical_offer_names_keep_separate_block_and_offer_ids(method):
    service, _, _ = service_for("repeated_offers", price="1500")
    result = run(service, method)
    assert len(result.items) == 2
    assert (
        len({item.provider_observations["analysis_v2"]["block_id"] for item in result.items}) == 2
    )
    assert result.document_metadata["analysis"]["observed_block_count"] == 2


def test_summary_with_identity_pending_is_partial():
    service, _, _ = service_for("no_catalog", price="1500")
    result = run(service, AnalysisMethod.OPENAI_STRUCTURED)
    assert result.document_metadata["analysis"]["identity_review_offer_count"] == 1
    assert result.document_metadata["analysis"]["status"] == "partial"


def test_reread_without_usable_price_does_not_remove_initial_valid_offer():
    service, reader, _ = service_for("one_reread", visual_gap=True)

    class DegradedAssembler:
        def assemble_interpretation(self, *, business_id, block, classification, candidates):
            initial = len(reader.requests) == 1
            return BlockInterpretationResult(
                block_id=block.block_id,
                offers=[
                    OfferInterpretation(
                        offer_id=f"{block.block_id}:offer",
                        block_id=block.block_id,
                        business_id=business_id,
                        raw_name="Artículo sintético",
                        offer_price=Decimal("1500") if initial else None,
                        currency="ARS",
                        price_type="unit" if initial else "unknown",
                        price_status="confirmed" if initial else "pending",
                    )
                ],
            )

    service.assembler = DegradedAssembler()
    result = run(service, AnalysisMethod.OPENAI_STRUCTURED)
    assert len(reader.requests) == 2
    assert len(result.items) == 1
    assert result.items[0].offer_price == Decimal("1500")
    assert result.items[0].price_type == "unit"
    assert len(result.document_metadata["analysis_ledger"]["blocks"]) == 2
    assert result.document_metadata["analysis"]["pending_block_count"] == 1


def test_percent_number_citation_is_not_accepted_as_absolute_price():
    text = "Arroz 10% descuento por unidad"
    block = OfferTextBlock(
        block_id="b",
        run_id="r",
        reading_id="read",
        page_number=1,
        order_on_page=0,
        full_text=text,
        fragments=[EvidenceFragment(fragment_id="f", text=text, order=0, legibility="readable")],
    )
    offer = OfferInterpretation(
        offer_id="o",
        block_id="b",
        business_id="tenant",
        raw_name="Arroz",
        offer_price=Decimal("10"),
        currency="ARS",
        price_type="unit",
        field_evidence={
            "raw_name": [EvidenceRef(fragment_id="f", quote="Arroz")],
            "offer_price": [EvidenceRef(fragment_id="f", quote="10", start=6, end=8)],
            "price_type": [EvidenceRef(fragment_id="f", quote="por unidad")],
        },
    )
    result = DocumentDecisionPolicy().validate_decision(
        request=OfferDecisionRequest(
            business_id="tenant", offer=offer, block=block, candidates=[], questions=[]
        ),
        decision=None,
    )
    assert result.offer.price_status == "pending"
    assert comparable_unit_price(offer=result.offer, block=block) is None


@pytest.mark.parametrize("case_id", ["reread_variant_loss", "reread_pack_degradation"])
def test_weaker_reread_preserves_all_variants_and_confirmed_pack(case_id):
    service, reader, _ = service_for(case_id, visual_gap=True)

    class WeakerAssembler:
        def assemble_interpretation(self, *, business_id, block, classification, candidates):
            initial = len(reader.requests) == 1
            if case_id == "reread_variant_loss":
                definitions = [("A", "100"), ("B", "200")] if initial else [("A", "100")]
            else:
                definitions = [("Bebida", "12000")]
            return BlockInterpretationResult(
                block_id=block.block_id,
                offers=[
                    OfferInterpretation(
                        offer_id=f"{block.block_id}:{name}",
                        block_id=block.block_id,
                        business_id=business_id,
                        raw_name=name,
                        offer_price=Decimal(price),
                        currency="ARS",
                        price_type="pack"
                        if case_id == "reread_pack_degradation" and initial
                        else "unit"
                        if case_id == "reread_variant_loss"
                        else "unknown",
                        package_quantity=6
                        if case_id == "reread_pack_degradation" and initial
                        else None,
                        price_status="confirmed"
                        if initial or case_id == "reread_variant_loss"
                        else "pending",
                    )
                    for name, price in definitions
                ],
            )

    service.assembler = WeakerAssembler()
    result = run(service, AnalysisMethod.OPENAI_STRUCTURED)
    assert len(reader.requests) == 2
    ledger = result.document_metadata["analysis_ledger"]
    assert len(ledger["blocks"]) == 2
    assert "reread_quality_regression" in ledger["blocks"][1]["reason_codes"]
    assert result.document_metadata["analysis"]["status"] == "partial"
    assert result.document_metadata["analysis"]["pending_block_count"] > 0
    if case_id == "reread_variant_loss":
        assert {(item.raw_name, item.offer_price) for item in result.items} == {
            ("A", Decimal("100")),
            ("B", Decimal("200")),
        }
    else:
        [item] = result.items
        assert item.price_type == "pack"
        assert item.package_quantity == 6
        assert item.offer_price == Decimal("12000")


def synthetic_offer(*, foreign_identity=False, price_confirmed=False):
    texts = ["BEBIDA EJEMPLO", "$ 12000 por bulto", "6 botellas", "Compra mínima 2 bultos"]
    fragments = [
        EvidenceFragment(fragment_id=f"f{index}", text=text, order=index, legibility="readable")
        for index, text in enumerate(texts)
    ]
    block = OfferTextBlock(
        block_id="block",
        run_id="run",
        reading_id="read",
        page_number=1,
        order_on_page=0,
        fragments=fragments,
        full_text="\n".join(texts),
    )
    offer = OfferInterpretation(
        offer_id="offer",
        block_id=block.block_id,
        business_id="tenant",
        raw_name=texts[0],
        offer_price=Decimal("12000"),
        price_type="pack",
        package_quantity=6,
        price_status="confirmed" if price_confirmed else "pending",
        minimum_purchase=MinimumPurchase(value=2, unit="pack", scope="offer"),
        field_evidence={
            "raw_name": [EvidenceRef(fragment_id="f0", quote=texts[0])],
            "offer_price": [EvidenceRef(fragment_id="f1", quote="$ 12000")],
            "price_type": [EvidenceRef(fragment_id="f1", quote="por bulto")],
            "package_quantity": [EvidenceRef(fragment_id="f2", quote="6 botellas")],
        },
    )
    candidate = IdentityCandidate(
        candidate_id="candidate",
        product_id="product",
        business_id="foreign" if foreign_identity else "tenant",
        name=texts[0],
        reason_codes=["confirmed_by_feedback"],
    )
    return block, offer, candidate


def test_policy_price_confirmation_does_not_accept_foreign_tenant_identity():
    block, offer, candidate = synthetic_offer(foreign_identity=True)
    result = DocumentDecisionPolicy().validate_decision(
        request=OfferDecisionRequest(
            business_id="tenant", block=block, offer=offer, candidates=[candidate], questions=[]
        ),
        decision=None,
    )
    assert result.offer.product_id is None
    assert result.offer.identity_status == "pending"
    assert result.offer.price_status == "pending"
    assert result.route == "needs_human_review"


def test_policy_price_and_identity_can_be_resolved_independently():
    block, offer, candidate = synthetic_offer()
    offer.field_evidence.pop("offer_price")
    result = DocumentDecisionPolicy().validate_decision(
        request=OfferDecisionRequest(
            business_id="tenant", block=block, offer=offer, candidates=[candidate], questions=[]
        ),
        decision=None,
    )
    assert result.offer.identity_status == "confirmed"
    assert result.offer.price_status == "pending"
    assert comparable_unit_price(offer=result.offer, block=block) is None


def test_minimum_quantity_reference_cannot_replace_pack_quantity():
    block, offer, _ = synthetic_offer(price_confirmed=True)
    assert comparable_unit_price(offer=offer, block=block) == Decimal("2000.00")
    offer.package_quantity = 2
    offer.field_evidence["package_quantity"] = [EvidenceRef(fragment_id="f3", quote="2 bultos")]
    assert comparable_unit_price(offer=offer, block=block) is None


@pytest.mark.parametrize("fault", ["jev_failure", "jev_partial", "invalid_probabilities"])
def test_real_b_components_with_fake_http_preserve_pending_and_attempt_metrics(fault, tmp_path):
    seen = []

    def transport(request, timeout, max_bytes):
        seen.append(json.loads(request.data))
        if fault == "jev_failure":
            raise TimeoutError("synthetic")
        answers = {}
        if fault == "invalid_probabilities":
            for question_id, question in seen[-1]["questions"].items():
                options = list(question["criteria"])
                answers[question_id] = {
                    "type": "choice",
                    "choice": options[0],
                    "confidence": 0.99,
                    "probabilities": {option: 2 for option in options},
                }
        return json.dumps({"model": "jev-1.13.0", "answers": answers}).encode()

    client = JevAnalysisClient(api_key="synthetic-key", transport=transport)
    provider = JevAnalysisProvider(client=client)
    reader = FakeReader(CASES[fault])
    service = SupplierOfferDocumentAnalysisService(
        reader=reader,
        assembler=DeterministicBlockAssembler(),
        classifier=provider,
        value_detector=detect_value_candidates,
        question_builder=lambda *, block, candidates: (
            prepare_block_request(business_id="synthetic-tenant", block=block).questions
        ),
    )
    result = run(service, AnalysisMethod.OPENAI_BLOCKS_JEV)
    ledger = result.document_metadata["analysis_ledger"]
    assert result.items == []
    assert len(seen) == 1
    assert len(reader.requests) == 1
    assert ledger["pending_blocks"][0]["text"] == "\n".join(CASES[fault]["fragments"])
    assert {call["provider"] for call in ledger["calls"]} == {"fake-openai", "jev"}
    assert result.document_metadata["analysis"]["status"] == "partial"
    path = tmp_path / "capture.json"
    path.write_text(json.dumps({"metadata": result.document_metadata}), encoding="utf-8")
    [report] = analyze([path], provenance="synthetic")["runs"]
    assert report["calls_observed_unique"] == 2
    assert report["usage"]["input_tokens"]["known_sum"] is None


def test_corpus_provenance_and_case_ids_are_explicit():
    assert CORPUS["provenance"] == "synthetic"
    assert CORPUS["human_labeled"] is False
    assert len(CASES) == len(CORPUS["cases"])


@pytest.mark.parametrize("method", list(AnalysisMethod))
def test_real_api_service_and_isolated_repository_keep_pending_pdf_and_tenant(method, monkeypatch):
    """Real API/service/persistence, fake reader/classifier/assembler, in-memory SQLite."""
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    service, reader, classifier = service_for("missing_price")
    classifier.acceptance_thresholds = None
    monkeypatch.setattr(main, "SessionLocal", sessions)
    monkeypatch.setattr(main, "build_document_analysis_service", lambda: service)
    monkeypatch.setattr(
        main,
        "review_extraction_packs",
        lambda *a, **kw: pytest.fail("Legacy visual reviewer must not run for V2"),
    )
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(settings, "internal_api_token", None)
    monkeypatch.setattr(settings, "kapso_api_key", None)
    monkeypatch.setattr(settings, "openai_api_key", "synthetic-key")
    monkeypatch.setattr(settings, "typesafe_api_key", "synthetic-key")
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(output)
    writer.close()
    try:
        with TestClient(main.create_app()) as client:
            response = client.post(
                "/procurement/supplier-offers/from-document",
                data={"analysis_method": method.value, "business_id": "synthetic-tenant",
                      "identity_probability": "0.91", "price_probability": "0.87"},
                files={"file": ("synthetic.pdf", output.getvalue(), "application/pdf")},
            )
            assert response.status_code == 200, response.text
            body = response.json()
            saved_ledger = body["import_result"]["document"]["metadata"]["analysis_ledger"]
            thresholds = saved_ledger["policy"]["thresholds"]
            assert classifier.acceptance_thresholds == thresholds
            assert thresholds["identity_probability"] == .91
            assert thresholds["price_probability"] == .87
            assert thresholds["confidence"] == .85
            assert service.policy is None  # request settings never mutate the service default
            assert body["analysis"]["method"] == method.value
            assert body["analysis"]["status"] == "partial"
            assert body["import_result"]["items"] == []
            assert body["pending_blocks"][0]["text"] == "\n".join(
                CASES["missing_price"]["fragments"]
            )
            document_id = body["import_result"]["document"]["id"]
            history = client.get(
                f"/procurement/supplier-offers/{document_id}/analysis",
                params={"business_id": "synthetic-tenant"},
            )
            assert history.status_code == 200
            assert history.json()["analysis"] == body["analysis"]
            assert history.json()["pending_blocks"] == body["pending_blocks"]
            foreign = client.get(
                f"/procurement/supplier-offers/{document_id}/analysis",
                params={"business_id": "other-synthetic-tenant"},
            )
            assert foreign.status_code == 404
            assert len(reader.requests) == 1  # POST only; history never rereads
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "method,scenario",
    [
        (AnalysisMethod.OPENAI_STRUCTURED, "clear"),
        (AnalysisMethod.OPENAI_BLOCKS_JEV, "clear"),
        (AnalysisMethod.OPENAI_STRUCTURED, "pending"),
        (AnalysisMethod.OPENAI_BLOCKS_JEV, "pending"),
        (AnalysisMethod.OPENAI_BLOCKS_JEV, "jev_failure"),
    ],
)
def test_real_builder_connects_reader_jev_policy_matching_and_persistence(
    method,
    scenario,
    monkeypatch,
    tmp_path,
):
    """Only reader.read and HTTP transport are replaced; the builder itself runs."""
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    repository = SqlProcurementRepository(sessions)
    name = "Arroz Ejemplo 1 kg"
    selected = repository.add_product(
        Product(
            id="synthetic-product",
            business_id="synthetic-tenant",
            name=name,
            normalized_name=normalize_text(name),
            unit_size=Decimal("1"),
            unit="kg",
            current_cost=Decimal("1800"),
        )
    )
    repository.add_product(
        Product(
            id="foreign-product",
            business_id="foreign-tenant",
            name=name,
            normalized_name=normalize_text(name),
            unit_size=Decimal("1"),
            unit="kg",
        )
    )
    monkeypatch.setattr(main, "SessionLocal", sessions)
    for setting, value in {
        "document_analysis_v2_enabled": True,
        "scheduler_enabled": False,
        "internal_api_token": None,
        "kapso_api_key": None,
        "openai_api_key": "synthetic-key",
        "typesafe_api_key": "synthetic-key",
        "jev_reconciliation_model": "jev-1.13.0",
    }.items():
        monkeypatch.setattr(settings, setting, value)
    monkeypatch.setattr(
        main,
        "review_extraction_packs",
        lambda *a, **kw: pytest.fail("Real builder V2 must not use legacy review"),
    )
    reads, posts = [], []

    def fake_read(self, *, request):
        reads.append(request)
        is_a = request.analysis_method == AnalysisMethod.OPENAI_STRUCTURED
        proposal = {
            "raw_name": name,
            "brand": None,
            "unit_size": 1,
            "unit": "kg",
            "offer_price": 1500,
            "currency": "ARS",
            "tax_included": None,
            "price_type": "unit",
            "package_quantity": None,
            "retail_contents_count": None,
            "minimum_purchase": None,
            "confidence_score": None,
            "warnings": [],
            "field_evidence": [
                {
                    "field": field,
                    "refs": [{"scope": "block", "fragment_index": index, "quote": quote}],
                }
                for field, index, quote in (
                    ("raw_name", 0, name),
                    ("unit_size", 0, "1 kg"),
                    ("unit", 0, "kg"),
                    ("offer_price", 1, "ARS 1500"),
                    ("currency", 1, "ARS"),
                    ("price_type", 1, "por unidad"),
                )
            ],
        }
        blocks = [
            {
                "fragments": [
                    {"text": text, "legibility": "readable"}
                    for text in (name, "ARS 1500 por unidad")
                ],
                "page_condition_indices": [],
                "warnings": [],
            }
        ]
        if is_a:
            blocks[0]["proposals"] = [proposal]
        if scenario == "pending":
            pending = {
                "fragments": [{"text": "Promo sin precio absoluto", "legibility": "readable"}],
                "page_condition_indices": [],
                "warnings": [],
            }
            if is_a:
                pending["proposals"] = []
            blocks.append(pending)
        wire = ReaderStructuredExtractionV2 if is_a else ReaderBlocksExtractionV2
        extraction = wire.model_validate(
            {
                "supplier_name": "Proveedor sintético",
                "warnings": [],
                "pages": [
                    {
                        "page_number": 1,
                        "page_conditions": [],
                        "blocks": blocks,
                        "coverage": {
                            "estimated_offer_blocks": len(blocks),
                            "needs_visual_review": False,
                            "warnings": [],
                        },
                    }
                ],
            }
        )
        return DocumentReadResult(
            extraction=extraction,
            call=ProviderCallRecord(
                call_id=f"fake-{request.reading_id}",
                run_id=request.run_id,
                reading_id=request.reading_id,
                provider="openai",
                model="synthetic-model",
                prompt_version="synthetic-reader",
                schema_version="supplier_offer_reader_v2",
                analysis_method=request.analysis_method,
                phase=request.phase,
                start_page=request.start_page,
                end_page=request.end_page,
                status="success",
            ),
        )

    def fake_http(request, timeout, max_bytes):
        payload = json.loads(request.data)
        posts.append(payload)
        if scenario == "jev_failure":
            raise TimeoutError("synthetic transport failure")
        occurrences = {c["candidate_id"]: c for c in payload["state"].get("occurrences", [])}
        answers = {}
        for question_id, question in payload["questions"].items():
            options = list(question["criteria"])
            purpose = question_id.rsplit(":", 1)[-1]
            if question_id.startswith("fragment:"):
                choice = "product_name" if question_id == "fragment:0" else "current_price"
            elif purpose == "role":
                occurrence = re.search(r"Evaluate occurrence '([^']+)'", question["instructions"])
                candidate = occurrences[occurrence[1]]
                choice = "current_price" if candidate["kind"] == "money" else "not_price"
            elif purpose in {"raw_name", "unit_size"}:
                kind = "text" if purpose == "raw_name" else "size"
                literal = name if purpose == "raw_name" else "1 kg"
                choice = next(
                    (
                        key
                        for key, c in occurrences.items()
                        if c["kind"] == kind and c["literal"] == literal
                    ),
                    "none",
                )
            elif purpose in {"price_type", "price_basis"}:
                choice = "unit"
            elif purpose == "basis_evidence":
                choice = next(
                    fragment["fragment_id"]
                    for fragment in payload["state"]["block"]["fragments"]
                    if fragment["text"] == "ARS 1500 por unidad"
                )
            elif purpose == "identity":
                choice = selected.id if selected.id in options else "none"
            elif purpose == "minimum_scope":
                choice = "unknown"
            else:
                choice = "none"
            assert choice in options, (question_id, choice, options)
            answers[question_id] = {
                "type": "choice",
                "choice": choice,
                "confidence": 0.99,
                "probabilities": {option: int(option == choice) for option in options},
            }
        return json.dumps(
            {
                "model": "jev-1.13.0",
                "answers": answers,
                "usage": {"input_tokens": 10, "output_tokens": 2},
            }
        ).encode()

    monkeypatch.setattr(OpenAISupplierOfferDocumentProvider, "read", fake_read)
    monkeypatch.setattr(jev_analysis, "_http_transport", fake_http)
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(output)
    writer.close()
    try:
        with TestClient(main.create_app()) as client:
            capabilities = client.get("/procurement/supplier-offers/analysis-methods").json()
            assert all(item["available"] for item in capabilities["methods"])
            assert not reads and not posts
            response = client.post(
                "/procurement/supplier-offers/from-document",
                data={"analysis_method": method.value, "business_id": "synthetic-tenant"},
                files={"file": ("synthetic.pdf", output.getvalue(), "application/pdf")},
            )
            assert response.status_code == 200, response.text
            body = response.json()
            summary = body["analysis"]
            assert summary["method"] == method.value
            document = body["import_result"]["document"]
            ledger = document["metadata"]["analysis_ledger"]
            assert document["metadata"]["analysis"] == summary
            assert len(reads) == 1
            if scenario == "jev_failure":
                assert body["import_result"]["items"] == []
                assert body["pending_blocks"][0]["text"] == f"{name}\nARS 1500 por unidad"
                [call] = [call for call in ledger["calls"] if call["provider"] == "jev"]
                assert call["status"] == "error"
                assert call["usage"]["input_tokens"] is None
            else:
                [item] = body["import_result"]["items"]
                assert Decimal(item["offer_price"]) == Decimal("1500")
                assert item["price_type"] == "unit", json.dumps(ledger["interpretations"])
                interpretation = ledger["interpretations"][0]
                assert interpretation["product_id"] == selected.id
                assert interpretation["identity_status"] == "confirmed"
                assert interpretation["price_status"] == "confirmed"
                assert summary["interpreted_offer_count"] == 1
                candidates = body["comparison"]["report"]["candidates"]
                assert candidates
                assert all(
                    candidate["product"]["id"] == selected.id
                    and candidate["product"]["business_id"] == "synthetic-tenant"
                    for candidate in candidates
                )
                if scenario == "pending":
                    assert body["pending_blocks"][0]["text"] == "Promo sin precio absoluto"
            assert summary["observed_block_count"] == (2 if scenario == "pending" else 1)
            assert summary["pending_block_count"] == (0 if scenario == "clear" else 1)
            assert summary["status"] == ("completed" if scenario == "clear" else "partial")
            history = client.get(
                f"/procurement/supplier-offers/{document['id']}/analysis",
                params={"business_id": "synthetic-tenant"},
            )
            assert history.json()["analysis"] == summary
            assert history.json()["pending_blocks"] == body["pending_blocks"]
            assert (
                client.get(
                    f"/procurement/supplier-offers/{document['id']}/analysis",
                    params={"business_id": "foreign-tenant"},
                ).status_code
                == 404
            )
            assert len(reads) == 1
            if method == AnalysisMethod.OPENAI_STRUCTURED:
                assert posts == []  # clear evidence and catalog identity bypass textual JEV
            else:
                assert posts
                audits = ledger["decision_audit"]
                assert audits
                calls_by_id = {call["call_id"]: call for call in ledger["calls"]}
                blocks_by_id = {block["block_id"]: block for block in ledger["blocks"]}
                for audit in audits:
                    assert audit["run_id"] == summary["run_id"]
                    assert audit["reading_id"] == blocks_by_id[audit["block_id"]]["reading_id"]
                    assert audit["questions"]
                    assert audit["call_ids"]
                    assert set(audit["call_ids"]) <= calls_by_id.keys()
                    assert all(
                        calls_by_id[call_id]["provider"] == "jev" for call_id in audit["call_ids"]
                    )
                    if scenario == "jev_failure":
                        assert audit["decisions"] == []
                        assert audit["reason_codes"]
                    else:
                        questions = {
                            question["question_id"]: question for question in audit["questions"]
                        }
                        assert audit["decisions"]
                        for decision in audit["decisions"]:
                            assert (
                                decision["choice"] in questions[decision["question_id"]]["options"]
                            )
                            assert sum(decision["probabilities"].values()) == 1
                            assert decision["probabilities"][decision["choice"]] == 1
                            assert decision["confidence"] == 0.99
                            assert decision["model"] == "jev-1.13.0"
                stored = repository.get_supplier_offer_document(
                    business_id="synthetic-tenant", supplier_offer_document_id=document["id"]
                )
                assert stored.metadata["analysis_ledger"]["decision_audit"] == audits
            capture = tmp_path / "real-components-synthetic-capture.json"
            capture.write_text(json.dumps(body), encoding="utf-8")
            [benchmark] = analyze([capture], provenance="synthetic")["runs"]
            assert benchmark["blocks"]["current"] == summary["observed_block_count"]
            assert benchmark["blocks"]["pending_current"] == summary["pending_block_count"]
            assert benchmark["calls_observed_unique"] == len(ledger["calls"])
    finally:
        engine.dispose()
