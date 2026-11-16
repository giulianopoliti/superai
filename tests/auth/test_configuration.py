from types import SimpleNamespace

import pytest

from app.auth.http import validate_auth_configuration


@pytest.mark.parametrize(
    "changes",
    [
        {"app_env": "production", "web_auth_enabled": False},
        {"app_env": "staging", "web_auth_enabled": False},
        {"app_env": "production", "web_cors_origins": ["http://localhost:8766"]},
        {"supabase_url": "http://example.supabase.co"},
        {"supabase_url": "https://example.supabase.co/other"},
        {"supabase_url": "https://user:password@example.supabase.co"},
        {"aws_origin_secret": "short"},
        {"web_cors_origins": ["*"]},
        {"web_cors_origins": ["https://anything.vercel.app"]},
        {"web_cors_origins": ["http://localhost:8766/"]},
    ],
)
def test_invalid_configuration_fails_closed(changes):
    values = {
        "app_env": "development",
        "web_auth_enabled": True,
        "supabase_url": "https://example.supabase.co",
        "aws_origin_secret": "a" * 32,
        "web_cors_origins": [],
    }
    values.update(changes)
    with pytest.raises(RuntimeError):
        validate_auth_configuration(SimpleNamespace(**values), object())


def test_requires_database():
    config = SimpleNamespace(
        app_env="production",
        web_auth_enabled=True,
        supabase_url="https://example.supabase.co",
        aws_origin_secret="a" * 32,
        web_cors_origins=[],
    )
    with pytest.raises(RuntimeError, match="database"):
        validate_auth_configuration(config, None)
