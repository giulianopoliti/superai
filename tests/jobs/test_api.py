from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import main
from app.auth.schemas import WebIdentity
from app.db.models import BackgroundJobModel, WebUserAccessModel
from app.jobs.repository import BackgroundJobRepository
from app.jobs.worker import AnalysisWorker
from app.modules.procurement.analysis_pipeline_schemas import PreparedAnalysis
from app.modules.procurement.schemas import ExtractedSupplierOfferItem, SupplierOfferExtraction
from app.settings import settings

SUBJECT = str(uuid4())
OTHER = str(uuid4())
SECRET = "origin-secret-for-isolated-job-tests-123456789"
HEADERS = {"X-Stock-AI-Origin": SECRET, "Authorization": "Bearer owner", "Idempotency-Key": "one"}


@pytest.fixture
def api(sessions, storage, monkeypatch):
    with sessions() as session:
        session.add_all(
            [
                WebUserAccessModel(auth_subject=SUBJECT, business_id="one", user_id="user-one"),
                WebUserAccessModel(auth_subject=OTHER, business_id="one", user_id="colleague"),
            ]
        )
        session.commit()
    for key, value in {
        "app_env": "production",
        "web_auth_enabled": True,
        "supabase_url": "https://example.supabase.co",
        "aws_origin_secret": SECRET,
        "web_cors_origins": [],
        "jobs_enabled": True,
        "job_files_dir": storage.root,
        "kapso_api_key": None,
        "kapso_webhook_secret": None,
        "scheduler_enabled": False,
        "llm_enabled": False,
        "openai_api_key": None,
        "gemini_api_key": None,
    }.items():
        monkeypatch.setattr(settings, key, value)
    monkeypatch.setattr(main, "SessionLocal", sessions)
    app = main.create_app()
    monkeypatch.setattr(
        app.state.web_token_verifier,
        "verify",
        lambda token: WebIdentity(SUBJECT if token == "owner" else OTHER),
    )
    with TestClient(app) as client:
        yield client


def upload(api, *, key="one", content=b"%PDF fixture", **fields):
    return api.post(
        "/procurement/supplier-offer-analysis-jobs",
        headers={**HEADERS, "Idempotency-Key": key},
        data={
            "business_id": "one",
            "supplier_name": "Proveedor",
            "extraction_provider": "local_text",
            **fields,
        },
        files={"file": ("offers.pdf", content, "application/pdf")},
    )


def test_creation_is_short_and_does_not_analyze(api, monkeypatch, storage):
    monkeypatch.setattr(
        main,
        "build_supplier_offer_analysis_pipeline",
        lambda: pytest.fail("HTTP creation must never analyze"),
    )
    response = upload(api)
    assert response.status_code == 202
    job = response.json()
    assert job["status"] == "queued"
    assert job["status_url"].startswith("/api/") and "business_id=one" in job["status_url"]
    assert "stockiando" not in response.text
    assert api.get(job["status_url"].removeprefix("/api"), headers=HEADERS).status_code == 200
    assert api.get(job["result_url"].removeprefix("/api"), headers=HEADERS).status_code == 409
    assert len(list(storage.root.glob("*.bin"))) == 1


def test_lost_202_response_is_recovered_by_idempotency_key(api, sessions, storage):
    first = upload(api).json()
    repeated = upload(api).json()
    assert first["job_id"] == repeated["job_id"]
    response = api.get(
        "/procurement/supplier-offer-analysis-jobs?business_id=one&idempotency_key=one",
        headers=HEADERS,
    )
    assert [row["job_id"] for row in response.json()["jobs"]] == [first["job_id"]]
    assert len(list(storage.root.glob("*.bin"))) == 1
    with sessions() as session:
        job = session.scalar(select(BackgroundJobModel))
        assert "Bearer" not in str(job.parameters) and SECRET not in str(job.parameters)
        assert job.file_sha256


@pytest.mark.parametrize("fields", [{"content": b"different"}, {"decision_margin": "0.3"}])
def test_reused_key_cannot_change_file_or_parameters(api, storage, fields):
    assert upload(api).status_code == 202
    assert upload(api, **fields).status_code == 409
    assert len(list(storage.root.glob("*.bin"))) == 1


def test_cross_tenant_and_cross_creator_jobs_are_not_revealed(api):
    job = upload(api).json()
    path = f"/procurement/supplier-offer-analysis-jobs/{job['job_id']}"
    assert api.get(path + "?business_id=two", headers=HEADERS).status_code == 403
    colleague = {**HEADERS, "Authorization": "Bearer colleague"}
    assert api.get(path + "?business_id=one", headers=colleague).status_code == 404
    assert api.get(path + "/result?business_id=one", headers=colleague).status_code == 404
    assert (
        api.get(
            "/procurement/supplier-offer-analysis-jobs?business_id=one", headers=colleague
        ).json()["jobs"]
        == []
    )


def test_end_to_end_job_result_keeps_the_analysis_contract(api, sessions, storage, monkeypatch):
    created = upload(api).json()
    pipeline = main.build_supplier_offer_analysis_pipeline()
    monkeypatch.setattr(
        pipeline,
        "prepare",
        lambda *_: PreparedAnalysis(
            business_id="one",
            supplier_name="Proveedor",
            request_id="fixture",
            elapsed_seconds=1,
            extraction=SupplierOfferExtraction(
                source_filename="offers.pdf",
                items=[
                    ExtractedSupplierOfferItem(raw_name="Azúcar 1kg", offer_price="1000"),
                ],
            ),
        ),
    )
    worker = AnalysisWorker(BackgroundJobRepository(sessions), storage, pipeline)
    assert worker.run_once()
    response = api.get(created["result_url"].removeprefix("/api"), headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["import_result"]["document"]["business_id"] == "one"
    assert response.headers["cache-control"] == "no-store"


def test_requests_are_validated_before_file_is_confirmed(api, storage, sessions):
    for fields in [
        {"key": ""},
        {"analysis_method": "unknown"},
        {"business_id": "two"},
        {"identity_probability": "2"},
        {"content": b""},
    ]:
        assert upload(api, **fields).status_code in {400, 403, 422}
    assert list(storage.root.iterdir()) == []
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(BackgroundJobModel)) == 0


def test_list_pagination_and_status_filter(api):
    for index in range(3):
        assert upload(api, key=str(index)).status_code == 202
    response = api.get(
        "/procurement/supplier-offer-analysis-jobs?business_id=one&limit=2&status=queued",
        headers=HEADERS,
    ).json()
    assert len(response["jobs"]) == 2 and response["next_offset"] == 2
    response = api.get(
        "/procurement/supplier-offer-analysis-jobs?business_id=one&limit=2&offset=2",
        headers=HEADERS,
    ).json()
    assert len(response["jobs"]) == 1 and response["next_offset"] is None


def test_synchronous_paid_analysis_is_not_a_fallback_when_jobs_are_enabled(api, monkeypatch):
    monkeypatch.setattr(
        main,
        "build_supplier_offer_analysis_pipeline",
        lambda: pytest.fail("Legacy route must not invoke providers"),
    )
    response = api.post(
        "/procurement/supplier-offers/from-document",
        headers=HEADERS,
        data={"business_id": "one"},
        files={"file": ("offers.pdf", b"%PDF fixture")},
    )
    assert response.status_code == 409
