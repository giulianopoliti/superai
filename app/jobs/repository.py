from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import defer

from app.db.models import BackgroundJobModel, utc_now
from app.db.repositories.sql_procurement import SqlProcurementRepository

ANALYSIS_JOB_TYPE = "supplier_offer_analysis_v1"


class IdempotencyConflict(Exception):
    pass


class LostLease(Exception):
    pass


class BorrowedSession:
    """Repository commits flush into the worker's single fenced transaction.

    Existing repositories keep their public interface. They cannot independently
    commit partial imports while the job result is being persisted.
    """

    def __init__(self, session):
        self.session = session

    def __getattr__(self, name):
        return getattr(self.session, name)

    def commit(self):
        self.session.flush()

    def close(self):
        pass


class BackgroundJobRepository:
    def __init__(self, session_factory, *, lease_seconds=120, max_attempts=3, clock=utc_now):
        self.sessions = session_factory
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.clock = clock

    def create(self, **values):
        try:
            with self.sessions() as session:
                job = BackgroundJobModel(id=str(uuid4()), **values)
                session.add(job)
                session.commit()
                session.refresh(job)
                return job, True
        except IntegrityError:
            with self.sessions() as session:
                existing = session.scalar(
                    select(BackgroundJobModel).where(
                        BackgroundJobModel.business_id == values["business_id"],
                        BackgroundJobModel.created_by_user_id == values["created_by_user_id"],
                        BackgroundJobModel.job_type == values["job_type"],
                        BackgroundJobModel.idempotency_key == values["idempotency_key"],
                    )
                )
                if existing is None:
                    raise
                if existing.request_fingerprint != values["request_fingerprint"]:
                    raise IdempotencyConflict from None
                return existing, False

    def get_owned(self, job_id, business_id, user_id, *, include_data=True):
        with self.sessions() as session:
            query = select(BackgroundJobModel).where(
                BackgroundJobModel.id == job_id,
                BackgroundJobModel.job_type == ANALYSIS_JOB_TYPE,
                BackgroundJobModel.business_id == business_id,
                BackgroundJobModel.created_by_user_id == user_id,
            )
            if not include_data:
                query = query.options(
                    defer(BackgroundJobModel.checkpoint),
                    defer(BackgroundJobModel.result),
                    defer(BackgroundJobModel.parameters),
                )
            return session.scalar(query)

    def list_owned(
        self, business_id, user_id, *, status=None, idempotency_key=None, limit=20, offset=0
    ):
        query = (
            select(BackgroundJobModel)
            .options(
                defer(BackgroundJobModel.checkpoint),
                defer(BackgroundJobModel.result),
                defer(BackgroundJobModel.parameters),
            )
            .where(
                BackgroundJobModel.business_id == business_id,
                BackgroundJobModel.job_type == ANALYSIS_JOB_TYPE,
                BackgroundJobModel.created_by_user_id == user_id,
            )
        )
        if status:
            query = query.where(BackgroundJobModel.status == status)
        if idempotency_key:
            query = query.where(BackgroundJobModel.idempotency_key == idempotency_key)
        with self.sessions() as session:
            return list(
                session.scalars(
                    query.order_by(
                        BackgroundJobModel.created_at.desc(), BackgroundJobModel.id.desc()
                    )
                    .limit(limit)
                    .offset(offset)
                )
            )

    def claim(self):
        now = self.clock()
        with self.sessions() as session, session.begin():
            job = session.scalar(
                select(BackgroundJobModel)
                .where(
                    BackgroundJobModel.status == "queued",
                    BackgroundJobModel.job_type == ANALYSIS_JOB_TYPE,
                    BackgroundJobModel.attempts < self.max_attempts,
                )
                .order_by(BackgroundJobModel.created_at, BackgroundJobModel.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            changed = session.execute(
                update(BackgroundJobModel)
                .where(
                    BackgroundJobModel.id == job.id,
                    BackgroundJobModel.status == "queued",
                )
                .values(
                    status="running",
                    phase="checkpointed" if job.checkpoint else "claimed",
                    attempts=BackgroundJobModel.attempts + 1,
                    attempt_token=str(uuid4()),
                    heartbeat_at=now,
                    lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                    updated_at=now,
                )
            )
            if changed.rowcount != 1:
                return None
            session.flush()
            session.refresh(job)
            session.expunge(job)
            return job

    def owned_attempt(self, job_id, token):
        return (
            BackgroundJobModel.id == job_id,
            BackgroundJobModel.job_type == ANALYSIS_JOB_TYPE,
            BackgroundJobModel.attempt_token == token,
            BackgroundJobModel.status == "running",
            BackgroundJobModel.lease_expires_at > self.clock(),
        )

    def change_attempt(self, job_id, token, **values):
        with self.sessions() as session, session.begin():
            result = session.execute(
                update(BackgroundJobModel)
                .where(
                    *self.owned_attempt(job_id, token),
                )
                .values(**values, updated_at=self.clock())
            )
            if result.rowcount != 1:
                raise LostLease

    def heartbeat(self, job_id, token):
        now = self.clock()
        self.change_attempt(
            job_id,
            token,
            heartbeat_at=now,
            lease_expires_at=now + timedelta(seconds=self.lease_seconds),
        )

    def checkpoint(self, job_id, token, prepared):
        self.change_attempt(
            job_id, token, checkpoint=prepared.model_dump(mode="json"), phase="checkpointed"
        )

    def fail(self, job_id, token, code, message):
        self.change_attempt(
            job_id,
            token,
            status="failed",
            phase="failed",
            error_code=code,
            error_message=message,
            finished_at=self.clock(),
            lease_expires_at=None,
        )

    def complete(self, job_id, token, persist):
        with self.sessions() as session, session.begin():
            job = session.scalar(
                select(BackgroundJobModel)
                .where(
                    *self.owned_attempt(job_id, token),
                )
                .with_for_update()
            )
            if job is None:
                raise LostLease
            # Do not hold a DB transaction over provider calls. Only deterministic
            # persistence runs here, under a row lock and a fresh bounded lease.
            deadline = self.clock() + timedelta(seconds=self.lease_seconds)
            job.lease_expires_at = deadline
            job.phase = "persisting"

            @contextmanager
            def borrowed():
                yield BorrowedSession(session)

            result = persist(SqlProcurementRepository(borrowed, use_auxiliary_catalog=False))
            if self.clock() >= deadline:
                raise LostLease
            job.result = result.model_dump(mode="json")
            job.checkpoint = (job.checkpoint or {}).get("continuation_checkpoint")
            job.status = "succeeded"
            job.phase = "completed"
            job.finished_at = self.clock()
            job.updated_at = self.clock()
            job.lease_expires_at = None
            # Result and every business write commit together on context exit.
            return result

    def continue_owned(self, job_id, business_id, user_id, *, source_document_id=None):
        """Explicit bounded continuation forks a new document; old reviews stay intact."""
        from app.modules.procurement.page_analysis_schemas import PipelineCheckpoint

        with self.sessions() as session, session.begin():
            job = session.scalar(select(BackgroundJobModel).where(
                BackgroundJobModel.id == job_id,
                BackgroundJobModel.business_id == business_id,
                BackgroundJobModel.created_by_user_id == user_id,
                BackgroundJobModel.job_type == ANALYSIS_JOB_TYPE,
            ).with_for_update())
            if job is None:
                raise LookupError("Job unavailable")
            if job.status in {"running", "queued"}:
                if source_document_id and (job.checkpoint or {}).get("analysis", {}).get(
                    "parent_run_id") != source_document_id:
                    raise ValueError("Continuation version mismatch")
                return job
            if job.status != "succeeded" or not job.checkpoint:
                raise ValueError("No continuation")
            checkpoint = PipelineCheckpoint.model_validate(job.checkpoint)
            state = checkpoint.analysis
            if source_document_id and state.run_id != source_document_id:
                raise ValueError("Continuation version mismatch")
            if state.continuation_round >= 2:
                raise ValueError("Continuation budget exhausted")
            parent = state.run_id
            state.parent_run_id, state.run_id = parent, uuid4().hex
            state.continuation_round += 1
            state.reserved_calls = 0
            for call in state.previous_calls:
                call.source_run_id = call.source_run_id or parent
                call.run_id = state.run_id
            for work in state.work:
                if work.result:
                    work.result.call.source_run_id = work.result.call.source_run_id or parent
                    work.result.call.run_id = state.run_id
                if work.status == "failed" and work.region is not None:
                    if work.result:
                        state.previous_calls.append(work.result.call)
                    work.status, work.result = "pending", None
            if not any(w.status == "pending" for w in state.work):
                raise ValueError("No pending reads")
            job.checkpoint = checkpoint.model_dump(mode="json")
            job.status, job.phase, job.attempts = "queued", "checkpointed", 0
            job.finished_at = job.attempt_token = None
            job.updated_at = self.clock()
            return job

    def reap_expired(self):
        now = self.clock()
        count = 0
        with self.sessions() as session, session.begin():
            jobs = list(
                session.scalars(
                    select(BackgroundJobModel)
                    .where(
                        BackgroundJobModel.status == "running",
                        BackgroundJobModel.job_type == ANALYSIS_JOB_TYPE,
                        BackgroundJobModel.lease_expires_at <= now,
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            for job in jobs:
                safe = job.checkpoint is not None or job.phase == "claimed"
                if safe and job.attempts < self.max_attempts:
                    job.status = "queued"
                    job.phase = "checkpointed" if job.checkpoint else "queued"
                else:
                    job.status = "failed"
                    job.phase = "failed"
                    job.error_code = (
                        "provider_result_uncertain" if not safe else "attempts_exhausted"
                    )
                    job.error_message = (
                        "La ejecución se interrumpió. Requiere revisión antes de volver a analizar."
                    )
                    job.finished_at = now
                job.attempt_token = None
                job.lease_expires_at = None
                job.updated_at = now
                count += 1
        return count

    def retention_candidates(self, cutoff):
        with self.sessions() as session:
            return list(
                session.scalars(
                    select(BackgroundJobModel)
                    .where(
                        BackgroundJobModel.status.in_(["succeeded", "failed"]),
                        BackgroundJobModel.job_type == ANALYSIS_JOB_TYPE,
                        BackgroundJobModel.finished_at < cutoff,
                    )
                    .limit(100)
                )
            )

    def remove_terminal(self, job_id, cutoff):
        with self.sessions() as session, session.begin():
            return session.execute(
                delete(BackgroundJobModel).where(
                    BackgroundJobModel.id == job_id,
                    BackgroundJobModel.job_type == ANALYSIS_JOB_TYPE,
                    BackgroundJobModel.status.in_(["succeeded", "failed"]),
                    BackgroundJobModel.finished_at < cutoff,
                )
            ).rowcount

    def referenced_files(self):
        with self.sessions() as session:
            return set(session.scalars(select(BackgroundJobModel.file_key)))
