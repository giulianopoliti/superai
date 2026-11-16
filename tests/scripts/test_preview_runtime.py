import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run_preview(tmp_path, code, **overrides):
    root = tmp_path / "fixtures"
    env = {
        **os.environ, "PYTHONPATH": str(ROOT), "STOCK_AI_PREVIEW_FIXTURES": "1",
        "PREVIEW_DATA_DIR": str(root),
        "DATABASE_URL": "sqlite+pysqlite:///" + (root / "fixtures.db").as_posix(),
        "SUPABASE_URL": "https://example.supabase.co",
        "AWS_ORIGIN_SECRET": "fixture-only-origin-secret-32-chars-long",
        "PREVIEW_AUTH_SUBJECT": "c35fbf1f-e995-4e80-8296-ae8829690401",
        "PREVIEW_ANALYSIS_DELAY_SECONDS": "0", **overrides,
    }
    return subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=30)


def test_preview_refuses_production_database_and_missing_opt_in(tmp_path):
    for overrides in ({"DATABASE_URL": "postgresql://must-not-connect.invalid/db"},
                      {"STOCK_AI_PREVIEW_FIXTURES": "0"}):
        result = run_preview(tmp_path, "from scripts.preview_runtime import configure; configure()",
                             **overrides)
        assert result.returncode != 0
        assert not (tmp_path / "fixtures" / "fixtures.db").exists()


def test_preview_refuses_dotenv_files(tmp_path):
    (tmp_path / ".env").write_text("OPENAI_API_KEY=must-not-read", encoding="utf-8")
    result = run_preview(tmp_path, "from scripts.preview_runtime import configure; configure()")
    assert result.returncode != 0
    assert "dotenv" in result.stderr


def test_preview_jobs_use_isolated_fixtures_and_no_paid_calls(tmp_path):
    result = run_preview(tmp_path, '''
from scripts.preview_runtime import seed, compose
seed()
backend, app = compose()
from app.auth.schemas import WebIdentity
from app.settings import settings
from app.jobs.worker import AnalysisWorker
from app.jobs.repository import BackgroundJobRepository
from app.jobs.storage import JobFileStorage
from app.db.session import SessionLocal
from fastapi.testclient import TestClient
assert not settings.openai_api_key and not settings.gemini_api_key
assert not settings.llm_enabled and not settings.scheduler_enabled
app.state.web_token_verifier.verify = lambda _: WebIdentity(
    "c35fbf1f-e995-4e80-8296-ae8829690401")
headers = {"X-Stock-AI-Origin": settings.aws_origin_secret, "Authorization": "Bearer fixture",
           "Idempotency-Key": "preview-smoke"}
with TestClient(app) as client:
    assert client.get("/auth/me").status_code == 403
    profile = client.get("/auth/me", headers=headers)
    assert profile.status_code == 200
    assert profile.json()["businesses"][0]["business_name"].startswith("Comercio de prueba")
    created = client.post("/procurement/supplier-offer-analysis-jobs", headers=headers,
        data={"business_id":"demo-business", "analysis_method":"openai_structured"},
        files={"file":("fixture.pdf",b"%PDF synthetic preview")})
    assert created.status_code == 202, created.text
    worker = AnalysisWorker(BackgroundJobRepository(SessionLocal), JobFileStorage(
        settings.job_files_dir), backend.build_supplier_offer_analysis_pipeline())
    assert worker.run_once()
    result = client.get(created.json()["result_url"].removeprefix("/api"), headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["analysis"]["provider_call_count"] == 0
    assert "simulado" in result.json()["extraction_warnings"][0]
''')
    assert result.returncode == 0, result.stderr + result.stdout
