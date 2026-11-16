"""Independent supervised worker. No bearer tokens are needed or persisted."""

import argparse
import hashlib
import logging
import signal
import threading
from datetime import timedelta
from types import SimpleNamespace

from app.db.models import utc_now
from app.jobs.repository import BackgroundJobRepository, LostLease
from app.jobs.storage import JobFileStorage
from app.modules.procurement.analysis_pipeline_schemas import AnalysisParameters, PreparedAnalysis

logger = logging.getLogger(__name__)


class AnalysisWorker:
    def __init__(self, repository, storage, pipeline, *, heartbeat_seconds=20):
        self.repository = repository
        self.storage = storage
        self.pipeline = pipeline
        self.heartbeat_seconds = heartbeat_seconds
        self.stopping = threading.Event()

    def run_once(self):
        self.repository.reap_expired()
        job = self.repository.claim()
        if job is None:
            return False
        finished = threading.Event()
        lost = threading.Event()

        def renew():
            while not finished.wait(self.heartbeat_seconds):
                try:
                    self.repository.heartbeat(job.id, job.attempt_token)
                except Exception:
                    lost.set()
                    return

        heartbeat = threading.Thread(target=renew, daemon=True)
        heartbeat.start()
        try:
            parameters = AnalysisParameters.model_validate(job.parameters)
            if parameters.business_id != job.business_id:
                raise ValueError("Job parameters belong to another business")
            page_checkpoint = job.checkpoint and job.checkpoint.get("kind") == "pipeline_pages_v1"
            if job.checkpoint is not None and not page_checkpoint:
                prepared = PreparedAnalysis.model_validate(job.checkpoint)
            else:
                self.repository.change_attempt(job.id, job.attempt_token, phase="extracting")
                with self.storage.path(job.file_key).open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != job.file_sha256:
                        raise ValueError("Stored file does not match its digest")
                    stream.seek(0)
                    kwargs = {}
                    if (parameters.extraction_provider in {"auto", "openai"}
                            and getattr(self.pipeline, "supports_page_checkpoints", False)):
                        def save_pages(state):
                            if lost.is_set():
                                raise LostLease
                            self.repository.checkpoint(job.id, job.attempt_token, state)

                        kwargs = {"checkpoint": job.checkpoint,
                                  "save_checkpoint": save_pages}
                    prepared = self.pipeline.prepare(
                        SimpleNamespace(filename=job.source_filename, file=stream), parameters,
                        **kwargs,
                    )
                if lost.is_set():
                    raise LostLease
                prepared.extraction.document_metadata["analysis_job_id"] = job.id
                self.repository.checkpoint(job.id, job.attempt_token, prepared)
            if lost.is_set():
                raise LostLease
            self.repository.complete(
                job.id,
                job.attempt_token,
                lambda repository: self.pipeline.persist(prepared, parameters, repository),
            )
            logger.info("background_job job_id=%s status=succeeded", job.id)
        except LostLease:
            logger.warning("background_job job_id=%s status=lease_lost", job.id)
        except Exception as exc:
            # Do not leak provider bodies, filenames, credentials or document text.
            logger.warning("background_job job_id=%s error_type=%s", job.id, type(exc).__name__)
            try:
                self.repository.fail(
                    job.id,
                    job.attempt_token,
                    "analysis_failed",
                    "No se pudo completar el análisis. Revisá el trabajo antes de repetirlo.",
                )
            except LostLease:
                pass
        finally:
            finished.set()
            heartbeat.join(timeout=6)
        return True

    def run(self, poll_seconds=2):
        while not self.stopping.is_set():
            try:
                worked = self.run_once()
            except Exception as exc:
                logger.warning("background_worker error_type=%s", type(exc).__name__)
                worked = False
            if not worked:
                self.stopping.wait(poll_seconds)

    def cleanup(self, *, retention_days=30, orphan_hours=24):
        # Only terminal jobs are eligible. Never remove queued/running files.
        cutoff = utc_now() - timedelta(days=retention_days)
        removed = 0
        for job in self.repository.retention_candidates(cutoff):
            if self.repository.remove_terminal(job.id, cutoff):
                self.storage.remove(job.file_key)
                removed += 1
        # Read the DB first; an unavailable DB must not trigger orphan deletion.
        referenced = self.repository.referenced_files()
        grace = (utc_now() - timedelta(hours=orphan_hours)).timestamp()
        for path in self.storage.root.iterdir():
            if path.suffix not in {".bin", ".upload"} or path.stat().st_mtime >= grace:
                continue
            if path.stem in referenced:
                continue
            try:
                self.storage.path(path.stem)  # Validate the generated UUID, never arbitrary paths.
            except ValueError:
                continue
            path.unlink()
        self.storage.sync_directory()
        return removed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()
    # Composition stays at the application boundary, not in the assistant core.
    from app.db.session import SessionLocal
    from app.main import build_supplier_offer_analysis_pipeline
    from app.settings import settings

    if SessionLocal is None or not settings.jobs_enabled:
        parser.error("Jobs and DATABASE_URL must be configured")
    logging.basicConfig(level=logging.INFO)
    worker = AnalysisWorker(
        BackgroundJobRepository(SessionLocal, lease_seconds=settings.job_lease_seconds),
        JobFileStorage(settings.job_files_dir),
        build_supplier_offer_analysis_pipeline(),
        heartbeat_seconds=settings.job_heartbeat_seconds,
    )
    signal.signal(signal.SIGTERM, lambda *_: worker.stopping.set())
    signal.signal(signal.SIGINT, lambda *_: worker.stopping.set())
    if args.cleanup:
        print(f"Removed {worker.cleanup(retention_days=settings.job_retention_days)} terminal jobs")
    elif args.once:
        worker.run_once()
    else:
        worker.run()


if __name__ == "__main__":
    main()
