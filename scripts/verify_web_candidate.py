"""Probe an isolated, loopback-only candidate without changing the live service."""

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-schema", default="20261030_0019")
    args = parser.parse_args()
    from dotenv import dotenv_values

    for key, value in dotenv_values(args.env_file).items():
        if value is not None:
            os.environ[key] = value
    os.environ.update(APP_ENV="production", WEB_AUTH_ENABLED="true", JOBS_ENABLED="true",
                      SCHEDULER_ENABLED="false", WEB_CORS_ORIGINS="[]",
                      SUPABASE_URL="https://uwyslofdvynmsdbsbpam.supabase.co",
                      AWS_ORIGIN_SECRET=secrets.token_urlsafe(48))
    from sqlalchemy import text

    from app.db.session import SessionLocal

    with SessionLocal() as session:
        version = session.scalar(text("SELECT version_num FROM alembic_version"))
        jobs = session.scalar(text("SELECT count(*) FROM background_jobs"))
        businesses = session.scalar(text("SELECT count(*) FROM businesses"))
    result = {"schema": version, "expected_schema": args.expected_schema,
              "database_read": True, "business_count": businesses,
              "existing_jobs": jobs, "public_service_changed": False, "paid_calls": 0}
    if version != args.expected_schema:
        raise RuntimeError("Unexpected schema revision")
    with tempfile.TemporaryDirectory(prefix="stock-ai-candidate-") as directory:
        os.environ["JOB_FILES_DIR"] = str(Path(directory) / "files")
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
        address = f"http://127.0.0.1:{port}"

        def status(path, *, origin=False, bearer=False):
            headers = {}
            if origin:
                headers["X-Stock-AI-Origin"] = os.environ["AWS_ORIGIN_SECRET"]
            if bearer:
                headers["Authorization"] = "Bearer invalid-probe-token"
            request = Request(address + path, headers=headers)
            try:
                with urlopen(request, timeout=5) as response:
                    return response.status
            except HTTPError as error:
                return error.code

        log_path = Path(directory) / "server.log"
        log_path.touch(mode=0o600)
        with log_path.open("wb") as log:
            server = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                 "--port", str(port)], stdout=log, stderr=log,
            )
            try:
                for _ in range(60):
                    if server.poll() is not None:
                        raise RuntimeError("Candidate process failed to start; inspect private log")
                    try:
                        if status("/health") == 200:
                            break
                    except URLError:
                        time.sleep(0.25)
                else:
                    raise RuntimeError("Candidate startup timed out")
                checks = {"health": status("/health"), "origin_missing": status("/auth/me"),
                          "bearer_missing": status("/auth/me", origin=True),
                          "invalid_bearer": status("/auth/me", origin=True, bearer=True),
                          "jobs_origin_missing": status(
                              "/procurement/supplier-offer-analysis-jobs?business_id=demo-business"
                          ), "openapi_disabled": status("/openapi.json")}
                expected = {"health": 200, "origin_missing": 403, "bearer_missing": 401,
                            "invalid_bearer": 401, "jobs_origin_missing": 403,
                            "openapi_disabled": 404}
                if checks != expected:
                    raise RuntimeError("Unexpected candidate HTTP protection")
                result["http"] = checks
                if jobs == 0:
                    worker = subprocess.run(
                        [sys.executable, "-m", "app.jobs.worker", "--once"],
                        stdout=log, stderr=log, timeout=30, check=True,
                    )
                    result["empty_queue_worker_exit"] = worker.returncode
                else:
                    result["empty_queue_worker_exit"] = "skipped: queue not empty"
            finally:
                server.terminate()
                try:
                    server.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Candidate verification failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
