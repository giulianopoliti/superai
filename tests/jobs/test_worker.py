import os
from datetime import timedelta

from sqlalchemy import func, select

from app import main
from app.db.models import SupplierOfferDocumentModel, utc_now
from app.jobs.repository import BackgroundJobRepository
from app.jobs.worker import AnalysisWorker
from app.modules.procurement.analysis_pipeline_schemas import PreparedAnalysis
from app.modules.procurement.schemas import ExtractedSupplierOfferItem, SupplierOfferExtraction


def prepared():
    return PreparedAnalysis(
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
    )


def test_worker_reuses_real_persistence_and_returns_existing_result_contract(
    sessions,
    storage,
    queued_job,
    monkeypatch,
):
    repository, job = queued_job()
    pipeline = main.build_supplier_offer_analysis_pipeline()
    calls = []
    monkeypatch.setattr(pipeline, "prepare", lambda *_: calls.append(True) or prepared())
    worker = AnalysisWorker(repository, storage, pipeline)
    assert worker.run_once()
    completed = repository.get_owned(job.id, "one", "user-one")
    assert completed.status == "succeeded"
    assert set(completed.result) >= {"import_result", "comparison", "analysis", "pending_blocks"}
    assert completed.result["import_result"]["items"][0]["raw_name"] == "Azúcar 1kg"
    assert not worker.run_once()
    assert calls == [True]
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(SupplierOfferDocumentModel)) == 1


def test_restart_with_checkpoint_does_not_call_providers(
    sessions, storage, queued_job, monkeypatch
):
    clock = [utc_now()]
    repository = BackgroundJobRepository(sessions, lease_seconds=30, clock=lambda: clock[0])
    _, job = queued_job(repository=repository)
    old = repository.claim()
    repository.checkpoint(job.id, old.attempt_token, prepared())
    clock[0] += timedelta(seconds=31)
    pipeline = main.build_supplier_offer_analysis_pipeline()

    def forbidden(*_):
        raise AssertionError("A checkpoint must never repeat extraction")

    monkeypatch.setattr(pipeline, "prepare", forbidden)
    worker = AnalysisWorker(repository, storage, pipeline)
    assert worker.run_once()
    assert repository.get_owned(job.id, "one", "user-one").status == "succeeded"


def test_worker_error_is_safe_and_terminal(storage, queued_job, monkeypatch):
    repository, job = queued_job()
    pipeline = main.build_supplier_offer_analysis_pipeline()

    def fail(*_):
        raise RuntimeError("SECRET_PROVIDER_TOKEN")

    monkeypatch.setattr(pipeline, "prepare", fail)
    assert AnalysisWorker(repository, storage, pipeline).run_once()
    failed = repository.get_owned(job.id, "one", "user-one")
    assert failed.status == "failed"
    assert "SECRET" not in failed.error_message
    assert repository.claim() is None


def test_cleanup_keeps_active_files_and_removes_old_orphans(storage, queued_job):
    repository, job = queued_job()
    import io

    orphan = storage.save(io.BytesIO(b"orphan"))
    old = (utc_now() - timedelta(days=2)).timestamp()
    for key in [job.file_key, orphan.key]:
        os.utime(storage.path(key), (old, old))
    worker = AnalysisWorker(repository, storage, None)
    worker.cleanup()
    assert storage.path(job.file_key).exists()
    assert not storage.path(orphan.key).exists()
