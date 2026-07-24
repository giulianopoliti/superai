from app import main
from app.providers.llm.gemini import GeminiLLMProvider
from app.providers.llm.openai import OpenAILLMProvider
from app.settings import settings


def test_build_llm_provider_uses_gemini_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_enabled", True)
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(settings, "gemini_api_key", "test-gemini-key")

    provider = main.build_llm_provider()

    assert isinstance(provider, GeminiLLMProvider)


def test_build_llm_provider_uses_openai_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_enabled", True)
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "test-openai-key")

    provider = main.build_llm_provider()

    assert isinstance(provider, OpenAILLMProvider)


def test_build_llm_provider_returns_none_without_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_enabled", True)
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(settings, "gemini_api_key", None)

    assert main.build_llm_provider() is None
