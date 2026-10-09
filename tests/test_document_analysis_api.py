import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db.models import Base
from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.modules.procurement.analysis_contracts import (
    AnalysisLedger,
    AnalysisSummary,
    DocumentReadResult,
    PendingBlock,
    ProviderCallRecord,
    ReaderBlocksExtractionV2,
    ReaderOfferProposalV2,
    ReaderStructuredExtractionV2,
    TokenUsage,
)
from app.modules.procurement.document_analysis import AnalysisMetadataTooLargeError
from app.modules.procurement.schemas import ExtractedSupplierOfferItem, SupplierOfferExtraction
from app.modules.procurement.supplier_offers import SupplierOfferService
from app.settings import settings


@pytest.fixture
def api(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(main, "SessionLocal", sessions)
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(settings, "kapso_api_key", None)
    monkeypatch.setattr(settings, "internal_api_token", None)
    monkeypatch.setattr(settings, "openai_api_key", "fake")
    monkeypatch.setattr(settings, "typesafe_api_key", "fake")
    with TestClient(main.create_app()) as client:
        yield client, SqlProcurementRepository(sessions)
    engine.dispose()


def test_capabilities_require_implementation_even_with_both_keys(api):
    client, _ = api
    body = client.get("/procurement/supplier-offers/analysis-methods").json()
    assert body["methods"][0]["available"]
    assert not body["methods"][1]["available"]
    assert body["methods"][1]["unavailable_reason_code"] == "method_disabled"
    assert "fake" not in str(body)


@pytest.mark.parametrize("method,status", [("wrong", 422), ("openai_blocks_jev", 503)])
def test_invalid_or_unavailable_rejected_before_provider_or_repository(
    api, monkeypatch, method, status
):
    client, _ = api

    def forbidden(*args, **kwargs):
        raise AssertionError("Must reject before constructing reader/repository")

    monkeypatch.setattr(main, "build_supplier_offer_document_provider", forbidden)
    monkeypatch.setattr(main, "build_procurement_repository", forbidden)
    response = client.post(
        "/procurement/supplier-offers/from-document",
        data={"business_id": "tenant", "analysis_method": method},
        files={"file": ("offers.pdf", b"fake", "application/pdf")},
    )
    assert response.status_code == status
    assert isinstance(response.json()["detail"], str)


@pytest.mark.parametrize("method", ["openai_structured", "openai_blocks_jev"])
@pytest.mark.parametrize("threshold_fields", [{}, {
    "identity_probability": "0.92", "price_probability": "0.89",
    "decision_confidence": "0.86", "decision_margin": "0.12",
}])
def test_pending_only_document_survives_post_and_tenant_safe_history(
    api, monkeypatch, method, threshold_fields
):
    client, _ = api
    pending = PendingBlock(
        block_id="b",
        page_number=1,
        order_on_page=0,
        text="Promo <img src=x> sin precio",
        reason_codes=["missing_price"],
        reason="Falta el precio.",
    )
    summary = AnalysisSummary(
        run_id="run",
        method=method,
        status="partial",
        page_count=1,
        observed_block_count=1,
        interpreted_offer_count=0,
        pending_block_count=1,
    )
    ledger = AnalysisLedger(
        run_id="run", business_id="tenant", method=method, pending_blocks=[pending]
    )
    ledger.invalid_evidence = [{"text": pending.text}]
    seen = []

    def analyze(**kwargs):
        seen.append(kwargs)
        return SupplierOfferExtraction(
            supplier_name="Proveedor",
            items=[],
            document_metadata={
                "analysis": summary.model_dump(mode="json"),
                "analysis_ledger": ledger.model_dump(mode="json"),
                "analysis_v2": True,
            },
        )

    monkeypatch.setattr(
        main,
        "build_document_analysis_service",
        lambda: SimpleNamespace(supports=lambda method: True, analyze=analyze),
    )
    monkeypatch.setattr(
        main, "review_extraction_packs", lambda *a, **kw: pytest.fail("V2 pack call")
    )
    response = client.post(
        "/procurement/supplier-offers/from-document",
        data={"business_id": "tenant", "analysis_method": method, **threshold_fields},
        files={"file": ("offers.pdf", b"fake", "application/pdf")},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["analysis"]["method"] == method
    assert result["analysis"]["usage"] is None
    assert not result["import_result"]["items"]
    assert result["pending_blocks"][0]["text"] == pending.text
    doc_id = result["import_result"]["document"]["id"]
    history = client.get(
        f"/procurement/supplier-offers/{doc_id}/analysis", params={"business_id": "tenant"}
    )
    assert history.json()["analysis"] == result["analysis"]
    assert history.json()["pending_blocks"] == result["pending_blocks"]
    foreign = client.get(
        f"/procurement/supplier-offers/{doc_id}/analysis", params={"business_id": "another-tenant"}
    )
    assert foreign.status_code == 404
    assert len(seen) == 1

    thresholds = seen[0]["decision_thresholds"]
    assert thresholds.allow_jev_acceptance is True
    for key, attr, default in [
        ("identity_probability", "identity_probability", .85),
        ("price_probability", "price_probability", .85),
        ("decision_confidence", "confidence", .85),
        ("decision_margin", "margin", .10),
    ]:
        assert getattr(thresholds, attr) == float(threshold_fields.get(key, default))


def test_legacy_local_text_and_old_document_do_not_invent_method(api):
    client, _ = api
    response = client.post(
        "/procurement/supplier-offers/from-document",
        data={
            "business_id": "tenant",
            "supplier_name": "Proveedor",
            "extraction_provider": "local_text",
        },
        files={"file": ("offers.txt", b"Aceite $1200\n", "text/plain")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["analysis"] is None
    doc_id = response.json()["import_result"]["document"]["id"]
    detail = client.get(
        f"/procurement/supplier-offers/{doc_id}/analysis", params={"business_id": "tenant"}
    ).json()
    assert detail["analysis"] is None
    assert detail["pending_blocks"] == []


def test_v2_nullable_commercial_quantity_never_reappears_from_name(api):
    _, repository = api
    item = ExtractedSupplierOfferItem(
        raw_name="Toallitas x48",
        offer_price=1000,
        price_type="unknown",
        selected_source="analysis_v2",
        provider_observations={
            "analysis_v2": {
                "offer_id": "stable-offer",
                "block_id": "block",
                "interpretation": {"price_status": "pending"},
            }
        },
    )
    result = SupplierOfferService(repository).create_offer_from_extraction(
        business_id="tenant",
        supplier_name="Proveedor",
        extraction=SupplierOfferExtraction(items=[item]),
    )
    stored = repository.list_supplier_offer_items(
        business_id="tenant",
        supplier_offer_document_id=result.document.id,
    )[0]
    assert stored.id == "stable-offer"
    assert stored.package_quantity is None
    assert stored.unit_size is None
    assert stored.unit is None
    assert stored.comparable_unit_price is None


def test_metadata_size_checked_before_any_write():
    class NoWrites:
        def upsert_supplier(self, *a, **kw):
            pytest.fail("oversized ledger must not write")

    with pytest.raises(AnalysisMetadataTooLargeError):
        SupplierOfferService(NoWrites(), max_metadata_bytes=50).create_offer_from_extraction(
            business_id="tenant",
            supplier_name="Proveedor",
            extraction=SupplierOfferExtraction(items=[], document_metadata={"text": "x" * 100}),
        )


def test_same_execution_persistence_is_idempotent_and_foreign_ledger_rejected(api):
    _, repository = api
    summary = AnalysisSummary(
        run_id="stable-run",
        method="openai_structured",
        status="partial",
        page_count=1,
        observed_block_count=0,
        interpreted_offer_count=0,
        pending_block_count=0,
    )
    ledger = AnalysisLedger(run_id="stable-run", business_id="tenant", method="openai_structured")
    extraction = SupplierOfferExtraction(
        items=[],
        document_metadata={
            "analysis_v2": True,
            "analysis": summary.model_dump(mode="json"),
            "analysis_ledger": ledger.model_dump(mode="json"),
        },
    )
    service = SupplierOfferService(repository)
    first = service.create_offer_from_extraction(
        business_id="tenant", supplier_name="Proveedor", extraction=extraction
    )
    second = service.create_offer_from_extraction(
        business_id="tenant", supplier_name="Proveedor", extraction=extraction
    )
    assert first.document.id == second.document.id == "stable-run"
    assert len(repository.list_supplier_offer_documents(business_id="tenant")) == 1
    with pytest.raises(ValueError, match="tenant"):
        service.create_offer_from_extraction(
            business_id="other", supplier_name="Proveedor", extraction=extraction
        )


@pytest.mark.parametrize(
    "attribute,code",
    [
        ("openai_api_key", "openai_not_configured"),
        ("typesafe_api_key", "jev_not_configured"),
    ],
)
def test_capability_reason_matches_missing_configuration(api, monkeypatch, attribute, code):
    client, _ = api
    monkeypatch.setattr(settings, attribute, None)
    response = client.get("/procurement/supplier-offers/analysis-methods").json()
    assert response["methods"][1]["unavailable_reason_code"] == code


@pytest.mark.parametrize("method", ["openai_structured", "openai_blocks_jev"])
def test_real_factory_reader_assembler_policy_and_persistence_offline(api, monkeypatch, method):
    client, repository = api
    monkeypatch.setattr(settings, "document_analysis_v2_enabled", True)
    reads, transports = [], []

    def read(self, *, request):
        reads.append(request)
        fields = {name: None for name in ReaderOfferProposalV2.model_fields}
        fields.update(
            raw_name="Aceite",
            offer_price="1200",
            currency="ARS",
            price_type="unit",
            warnings=[],
            field_evidence=[
                {
                    "field": field,
                    "refs": [{"scope": "block", "fragment_index": idx, "quote": quote}],
                }
                for field, idx, quote in [
                    ("raw_name", 0, "Aceite"),
                    ("offer_price", 1, "$1200"),
                    ("price_type", 1, "por unidad"),
                ]
            ],
        )
        block = {
            "fragments": [
                {"text": "Aceite", "legibility": "readable"},
                {"text": "$1200 por unidad", "legibility": "readable"},
            ],
            "page_condition_indices": [],
            "warnings": [],
        }
        wire = ReaderBlocksExtractionV2
        if request.analysis_method == "openai_structured":
            wire = ReaderStructuredExtractionV2
            block["proposals"] = [fields]
        extraction = wire(
            supplier_name="Proveedor",
            warnings=[],
            pages=[
                {
                    "page_number": 1,
                    "page_conditions": [],
                    "blocks": [block],
                    "coverage": {
                        "estimated_offer_blocks": 1,
                        "needs_visual_review": False,
                        "warnings": [],
                    },
                }
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
                start_page=request.start_page,
                end_page=request.end_page,
                status="success",
                usage=TokenUsage(input_tokens=100, output_tokens=10, total_tokens=110),
            ),
        )

    def transport(request, timeout, max_bytes):
        payload = json.loads(request.data)
        transports.append(payload)
        answers = {}
        for key, question in payload["questions"].items():
            options = question["criteria"]
            choice = "none" if "none" in options else next(iter(options))
            if key == "fragment:0":
                choice = "product_name"
            elif key == "fragment:1" or key.endswith(":role"):
                choice = "current_price"
            elif key.endswith(":raw_name"):
                choice = next(
                    occurrence["candidate_id"]
                    for occurrence in payload["state"]["occurrences"]
                    if occurrence["candidate_id"] in options and occurrence["literal"] == "Aceite"
                )
            elif key.endswith(":basis_evidence"):
                choice = next(
                    fragment["fragment_id"]
                    for fragment in payload["state"]["block"]["fragments"]
                    if "por unidad" in fragment["text"]
                )
            elif key.endswith(":price_type") or key.endswith(":price_basis"):
                choice = "unit"
            elif key.endswith(":minimum_scope"):
                choice = "unknown"
            answers[key] = {
                "type": "choice",
                "choice": choice,
                "confidence": 0.8,
                "probabilities": {o: int(o == choice) for o in options},
            }
        return json.dumps(
            {
                "model": payload["model"],
                "answers": answers,
                "usage": {"input_tokens": 50, "output_tokens": 0},
            }
        ).encode()

    from app.providers import jev_analysis

    monkeypatch.setattr(main.OpenAISupplierOfferDocumentProvider, "read", read)
    monkeypatch.setattr(jev_analysis, "_http_transport", transport)
    monkeypatch.setattr(
        main,
        "review_extraction_packs",
        lambda *a, **kw: pytest.fail("V2 must not use pack reviewer"),
    )
    # Both routes are constructed by the real factory; capabilities perform no paid/DB work.
    capabilities = client.get("/procurement/supplier-offers/analysis-methods").json()
    assert all(m["available"] for m in capabilities["methods"])
    assert not reads and not transports
    response = client.post(
        "/procurement/supplier-offers/from-document",
        data={"business_id": "tenant", "analysis_method": method},
        files={"file": ("offers.png", b"fake", "image/png")},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["analysis"]["method"] == method
    assert result["analysis"]["status"] == "partial"  # no catalog identity
    assert result["analysis"]["identity_review_offer_count"] == 1
    assert len(result["import_result"]["items"]) == 1, result
    assert result["import_result"]["items"][0]["offer_price"] == "1200"
    assert len(reads) == 1
    if method == "openai_blocks_jev":
        assert transports
    document_id = result["import_result"]["document"]["id"]
    stored = repository.get_supplier_offer_document(
        business_id="tenant",
        supplier_offer_document_id=document_id,
    )
    assert stored.metadata["analysis"] == result["analysis"]
    assert (
        stored.metadata["analysis_ledger"]["blocks"][0]["full_text"] == "Aceite\n$1200 por unidad"
    )


@pytest.mark.parametrize("field", [
    "identity_probability", "price_probability", "decision_confidence", "decision_margin",
])
@pytest.mark.parametrize("value", ["-0.1", "1.1", "nan", "inf", "invalid"])
def test_invalid_thresholds_rejected_before_service(api, monkeypatch, field, value):
    client, _ = api
    monkeypatch.setattr(main, "build_document_analysis_service",
                        lambda: pytest.fail("Invalid request must not construct providers"))
    response = client.post(
        "/procurement/supplier-offers/from-document",
        data={"business_id": "tenant", field: value},
        files={"file": ("offers.pdf", b"fake", "application/pdf")},
    )
    assert response.status_code == 422
