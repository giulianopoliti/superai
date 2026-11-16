from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from scripts.local_web_runtime import LocalWebApp, public_configuration


def local_client(tmp_path):
    backend = FastAPI()

    @backend.get("/auth/me")
    def profile(request: Request):
        if request.headers.get("x-stock-ai-origin") != "server-only-secret":
            from fastapi import HTTPException
            raise HTTPException(403)
        if request.headers.get("authorization") != "Bearer fixture":
            from fastapi import HTTPException
            raise HTTPException(401)
        return {"businesses": [{"business_id": "allowed"}]}

    bundle = tmp_path / "auth.js"
    bundle.write_text("/* compiled login */")
    app = LocalWebApp(backend, supabase_url="https://example.supabase.co",
                      publishable_key="sb_publishable_fixture",
                      origin_secret="server-only-secret", auth_bundle=bundle)
    return TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 43210))


def test_local_config_and_bundle_contain_no_server_secret(tmp_path):
    with local_client(tmp_path) as client:
        config = client.get("/ui/config.js")
        assert config.status_code == 200
        assert '"authEnabled": true' in config.text
        assert '"apiBaseUrl": "/api"' in config.text
        assert "server-only-secret" not in config.text
        assert config.headers["cache-control"] == "no-store"
        assert client.get("/ui/auth.js").text == "/* compiled login */"


def test_proxy_keeps_bearer_required_and_replaces_injected_origin(tmp_path):
    with local_client(tmp_path) as client:
        assert client.get("/api/auth/me").status_code == 401
        direct = client.get("/auth/me", headers={"Authorization": "Bearer fixture"})
        assert direct.status_code == 403
        profile = client.get("/api/auth/me", headers={
            "Authorization": "Bearer fixture", "X-Stock-AI-Origin": "attacker",
        })
        assert profile.status_code == 200
        assert profile.json()["businesses"][0]["business_id"] == "allowed"


def test_local_proxy_rejects_external_hosts_origins_and_clients(tmp_path):
    with local_client(tmp_path) as client:
        for headers in ({"Host": "external.example"}, {"Origin": "https://external.example"},
                        {"Origin": "http://localhost:9999"}, {"Origin": "null"}):
            assert client.get("/ui/config.js", headers=headers).status_code == 403
        same_origin = client.get("/ui/config.js", headers={"Origin": "http://127.0.0.1:8000"})
        assert same_origin.status_code == 200
        client._transport.client = ("192.0.2.5", 1234)
        assert client.get("/api/auth/me").status_code == 403


def test_public_config_rejects_secret_keys(monkeypatch):
    import pytest

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_secret_never_public")
    with pytest.raises(RuntimeError, match="SUPABASE_PUBLISHABLE_KEY"):
        public_configuration()
