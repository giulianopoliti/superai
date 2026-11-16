"""Offline ASGI/worker smoke with a slow fake provider and a private SQLite file.

No real database, HTTP server, documents or paid providers are used. A duration
over 120 seconds exercises actual heartbeats while status requests stay short.
"""

import argparse
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=2)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 600:
        parser.error("--seconds must be between 1 and 600")
    with tempfile.TemporaryDirectory(prefix="stock-ai-jobs-smoke-") as directory:
        root = Path(directory)
        secret = uuid4().hex + uuid4().hex
        os.environ.update(
            {
                "APP_ENV": "test",
                "DATABASE_URL": "sqlite+pysqlite:///" + (root / "jobs.db").as_posix(),
                "WEB_AUTH_ENABLED": "true",
                "SUPABASE_URL": "https://example.supabase.co",
                "AWS_ORIGIN_SECRET": secret,
                "WEB_CORS_ORIGINS": "[]",
                "JOBS_ENABLED": "true",
                "JOB_FILES_DIR": str(root / "files"),
                "JOB_LEASE_SECONDS": "30",
                "JOB_HEARTBEAT_SECONDS": "1",
                "SCHEDULER_ENABLED": "false",
                "LLM_ENABLED": "false",
                "OPENAI_API_KEY": "",
                "GEMINI_API_KEY": "",
                "JEV_API_KEY": "",
                "TYPESAFE_AI_API_KEY": "",
                "KAPSO_API_KEY": "",
                "DOCUMENT_ANALYSIS_V2_ENABLED": "false",
            }
        )
        # Imports follow the environment override; no production DB can be created.
        from fastapi.testclient import TestClient

        from app import main as app_main
        from app.auth.schemas import WebIdentity
        from app.db.models import Base, BusinessModel, UserModel, WebUserAccessModel
        from app.db.session import SessionLocal
        from app.jobs.repository import BackgroundJobRepository
        from app.jobs.storage import JobFileStorage
        from app.jobs.worker import AnalysisWorker
        from app.modules.procurement.analysis_pipeline_schemas import PreparedAnalysis
        from app.modules.procurement.schemas import (
            ExtractedSupplierOfferItem,
            SupplierOfferExtraction,
        )

        Base.metadata.create_all(SessionLocal.kw["bind"])
        subject = str(uuid4())
        with SessionLocal() as session:
            session.add(BusinessModel(id="smoke", name="Fixture"))
            session.flush()
            session.add(UserModel(id="smoke-user", business_id="smoke", display_name="Fixture"))
            session.flush()
            session.add(
                WebUserAccessModel(auth_subject=subject, business_id="smoke", user_id="smoke-user")
            )
            session.commit()
        app_main.app.state.web_token_verifier.verify = lambda _: WebIdentity(subject)
        pipeline = app_main.build_supplier_offer_analysis_pipeline()

        def prepare(*_):
            time.sleep(args.seconds)
            return PreparedAnalysis(
                business_id="smoke",
                supplier_name="Fixture",
                request_id="smoke",
                elapsed_seconds=args.seconds,
                extraction=SupplierOfferExtraction(
                    source_filename="fixture.pdf",
                    items=[
                        ExtractedSupplierOfferItem(raw_name="Azúcar 1kg", offer_price="1000"),
                    ],
                ),
            )

        pipeline.prepare = prepare
        repository = BackgroundJobRepository(SessionLocal, lease_seconds=30)
        heartbeats = []
        original_heartbeat = repository.heartbeat

        def heartbeat(*arguments):
            original_heartbeat(*arguments)
            heartbeats.append(time.monotonic())

        repository.heartbeat = heartbeat
        worker = AnalysisWorker(
            repository, JobFileStorage(root / "files"), pipeline, heartbeat_seconds=1
        )
        headers = {
            "Authorization": "Bearer fixture",
            "X-Stock-AI-Origin": secret,
            "Idempotency-Key": "offline-smoke",
        }
        durations = []
        with TestClient(app_main.app) as client:
            created = client.post(
                "/procurement/supplier-offer-analysis-jobs",
                headers=headers,
                data={"business_id": "smoke", "extraction_provider": "local_text"},
                files={"file": ("fixture.pdf", b"%PDF fixture")},
            )
            assert created.status_code == 202, created.text
            job = created.json()
            thread = threading.Thread(target=worker.run_once, daemon=True)
            started = time.monotonic()
            thread.start()
            while thread.is_alive():
                before = time.monotonic()
                status = client.get(job["status_url"].removeprefix("/api"), headers=headers)
                durations.append(time.monotonic() - before)
                assert status.status_code == 200, status.text
                if time.monotonic() - started > args.seconds + 30:
                    raise TimeoutError("Worker did not finish")
                thread.join(timeout=1)
            result = client.get(job["result_url"].removeprefix("/api"), headers=headers)
            assert result.status_code == 200, result.text
            elapsed = time.monotonic() - started
            assert max(durations) < 5
            assert len(heartbeats) >= max(1, int(args.seconds) - 5)
        report = {
            "environment": "offline_asgi_sqlite",
            "simulated_provider": True,
            "provider_delay_seconds": args.seconds,
            "worker_elapsed_seconds": round(elapsed, 3),
            "heartbeats": len(heartbeats),
            "status_requests": len(durations),
            "max_status_seconds": round(max(durations), 4),
            "create_http_status": 202,
            "result_http_status": result.status_code,
            "result_contract_keys": sorted(result.json()),
            "paid_calls": 0,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report))
        SessionLocal.kw["bind"].dispose()


if __name__ == "__main__":
    main()
