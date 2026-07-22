from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app import main
from app.settings import settings


@pytest.fixture
def client(monkeypatch) -> Iterator[TestClient]:
    monkeypatch.setattr(settings, "kapso_api_key", None)
    monkeypatch.setattr(settings, "llm_enabled", False)
    monkeypatch.setattr(settings, "openai_api_key", None)
    monkeypatch.delenv("KAPSO_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(main, "SessionLocal", None)
    with TestClient(main.create_app()) as test_client:
        yield test_client
