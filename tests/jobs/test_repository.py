from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.db.models import BackgroundJobModel, SupplierModel, utc_now
from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.jobs.repository import BackgroundJobRepository, IdempotencyConflict, LostLease
from app.modules.procurement.analysis_pipeline_schemas import PreparedAnalysis
from app.modules.procurement.schemas import Product, Supplier, SupplierOfferExtraction


def checkpoint(business_id="one"):
    return PreparedAnalysis(
        business_id=business_id,
        supplier_name="Proveedor",
        extraction=SupplierOfferExtraction(items=[]),
        request_id="execution",
        elapsed_seconds=1,
    )


def test_idempotency_returns_original_and_conflicts(sessions, queued_job):
    repository, job = queued_job(key="repeat")
    values = {
        column: getattr(job, column)
        for column in [
            "business_id",
            "created_by_user_id",
            "job_type",
            "idempotency_key",
            "request_fingerprint",
            "file_key",
            "file_sha256",
            "source_filename",
            "parameters",
        ]
    }
    repeated, created = repository.create(**values)
    assert not created and repeated.id == job.id
    with pytest.raises(IdempotencyConflict):
        repository.create(**{**values, "request_fingerprint": "b" * 64})
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(BackgroundJobModel)) == 1


def test_claims_are_disjoint_and_lease_has_attempt_token(sessions, queued_job):
    repository, first = queued_job()
    _, second = queued_job()
    other = BackgroundJobRepository(sessions)
    claimed = [repository.claim(), other.claim()]
    assert {row.id for row in claimed} == {first.id, second.id}
    assert all(row.attempts == 1 and row.attempt_token for row in claimed)
    assert other.claim() is None


def test_reaper_never_repeats_uncertain_provider_calls(sessions, queued_job):
    clock = [utc_now()]
    repository = BackgroundJobRepository(sessions, lease_seconds=30, clock=lambda: clock[0])
    _, job = queued_job(repository=repository)
    attempt = repository.claim()
    repository.change_attempt(job.id, attempt.attempt_token, phase="extracting")
    clock[0] += timedelta(seconds=31)
    assert repository.reap_expired() == 1
    stored = repository.get_owned(job.id, "one", "user-one")
    assert stored.status == "failed" and stored.error_code == "provider_result_uncertain"
    assert repository.claim() is None


def test_reaper_recovers_checkpoint_and_fences_old_worker(sessions, queued_job):
    clock = [utc_now()]
    repository = BackgroundJobRepository(sessions, lease_seconds=30, clock=lambda: clock[0])
    _, job = queued_job(repository=repository)
    old = repository.claim()
    repository.checkpoint(job.id, old.attempt_token, checkpoint())
    clock[0] += timedelta(seconds=31)
    repository.reap_expired()
    new = repository.claim()
    assert new.id == job.id and new.attempt_token != old.attempt_token
    assert new.checkpoint["request_id"] == "execution"
    for operation in [
        lambda: repository.heartbeat(job.id, old.attempt_token),
        lambda: repository.checkpoint(job.id, old.attempt_token, checkpoint()),
        lambda: repository.complete(
            job.id, old.attempt_token, lambda _: pytest.fail("Stale write")
        ),
    ]:
        with pytest.raises(LostLease):
            operation()


def test_persistence_and_result_rollback_as_one_transaction(sessions, queued_job):
    repository, job = queued_job()
    attempt = repository.claim()

    def persist(bound):
        bound.add_supplier(
            Supplier(business_id="one", name="Proveedor", normalized_name="proveedor")
        )
        raise RuntimeError("Crash after repository commit")

    with pytest.raises(RuntimeError):
        repository.complete(job.id, attempt.attempt_token, persist)
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(SupplierModel)) == 0
    assert repository.get_owned(job.id, "one", "user-one").result is None


def test_job_persistence_uses_original_catalog_without_auxiliary_reads(
    sessions, queued_job, monkeypatch,
):
    from app.db.repositories import sql_procurement

    p = SqlProcurementRepository(sessions).add_product(Product(
        business_id="one", name="Cafe Arlistan 85 g", normalized_name="cafe arlistan 85 g",
    ))

    def forbidden(*args, **kwargs):
        pytest.fail("Background comparison must not read auxiliary classifications")

    monkeypatch.setattr(sql_procurement, "current_interpretations", forbidden)
    repository, job = queued_job()
    attempt = repository.claim()

    def persist(bound):
        loaded = bound.get_product(business_id="one", product_id=p.id)
        assert loaded.name == p.name and loaded.auxiliary is None
        return SimpleNamespace(model_dump=lambda **_: {"product_id": loaded.id})

    repository.complete(job.id, attempt.attempt_token, persist)
    assert repository.get_owned(job.id, "one", "user-one").result == {"product_id": p.id}


def test_lease_expiry_during_persistence_rolls_back(sessions, queued_job):
    clock = [utc_now()]
    repository = BackgroundJobRepository(sessions, lease_seconds=30, clock=lambda: clock[0])
    _, job = queued_job(repository=repository)
    attempt = repository.claim()

    def persist(bound):
        bound.add_supplier(
            Supplier(business_id="one", name="Proveedor", normalized_name="proveedor")
        )
        clock[0] += timedelta(seconds=31)
        return SimpleNamespace(model_dump=lambda **_: {"ok": True})

    with pytest.raises(LostLease):
        repository.complete(job.id, attempt.attempt_token, persist)
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(SupplierModel)) == 0


def test_results_and_list_are_scoped_to_creator_and_business(queued_job):
    repository, job = queued_job()
    assert repository.get_owned(job.id, "two", "user-two") is None
    assert repository.get_owned(job.id, "one", "colleague") is None
    assert repository.list_owned("one", "colleague") == []
    assert repository.list_owned("two", "user-two") == []


def test_checkpoint_retry_budget_is_bounded(sessions, queued_job):
    clock = [utc_now()]
    repository = BackgroundJobRepository(
        sessions, lease_seconds=30, max_attempts=2, clock=lambda: clock[0]
    )
    _, job = queued_job(repository=repository)
    for _ in range(2):
        attempt = repository.claim()
        repository.checkpoint(job.id, attempt.attempt_token, checkpoint())
        clock[0] += timedelta(seconds=31)
        repository.reap_expired()
    assert repository.claim() is None
    assert repository.get_owned(job.id, "one", "user-one").error_code == "attempts_exhausted"
