"""Authenticated local UI. Run with python -m scripts.local_web_runtime.

The origin secret stays in this process; the browser uses /api on loopback.
This composition must never be used as a public deployment.
"""

import argparse
import ipaddress
import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values
from starlette.responses import FileResponse, JSONResponse, Response

ROOT = Path(__file__).resolve().parents[1]
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def public_configuration():
    values = {}
    for name in (".env", ".data/local-web.env", ".env.local"):
        values.update(dotenv_values(ROOT / name))
    values.update(os.environ)
    url = values.get("SUPABASE_URL", "") or ""
    key = values.get("SUPABASE_PUBLISHABLE_KEY", "") or ""
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment or not key.startswith("sb_publishable_")):
        raise RuntimeError("Configure SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY in .env.local")
    return url.rstrip("/"), key


def build_auth_bundle():
    node = shutil.which("node")
    if not node:
        raise RuntimeError("Node.js is required to compile the local login")
    destination = ROOT / ".data/local-web/auth.js"
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        node, str(ROOT / "node_modules/esbuild/bin/esbuild"),
        str(ROOT / "frontend/auth-entry.mjs"), "--bundle", "--minify",
        "--platform=browser", "--format=iife", "--target=es2020",
        "--outfile=" + str(destination),
    ], cwd=ROOT, check=True)
    return destination


class LocalWebApp:
    def __init__(self, backend, *, supabase_url, publishable_key, origin_secret,
                 auth_bundle, jobs_enabled=False):
        self.backend = backend
        self.origin_secret = origin_secret
        self.auth_bundle = auth_bundle
        self.config = {
            "authEnabled": True, "jobsEnabled": jobs_enabled, "apiBaseUrl": "/api",
            "supabaseUrl": supabase_url, "supabasePublishableKey": publishable_key,
        }

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.backend(scope, receive, send)
        if scope["type"] != "http":
            return
        headers = scope.get("headers", [])
        hosts = [v.decode("latin-1") for k, v in headers if k.lower() == b"host"]
        origins = [v.decode("latin-1") for k, v in headers if k.lower() == b"origin"]
        client = scope.get("client")
        try:
            local_client = client and ipaddress.ip_address(client[0]).is_loopback
            host = urlsplit("http://" + hosts[0]) if len(hosts) == 1 else None
            valid_host = (host and host.hostname in LOCAL_HOSTS and not host.username
                          and not host.password and not host.path and not host.query
                          and not host.fragment)
            valid_origin = not origins or (
                len(origins) == 1 and origins[0] == "http://" + hosts[0]
            )
        except ValueError:
            local_client = valid_host = valid_origin = False
        if not (local_client and valid_host and valid_origin):
            return await JSONResponse({"detail": "Acceso local no autorizado."}, 403)(
                scope, receive, send)
        path = scope["path"]
        if path in {"/ui/config.js", "/ui/auth.js"}:
            if scope["method"] not in {"GET", "HEAD"}:
                response = Response(status_code=405)
            elif path.endswith("config.js"):
                response = Response(
                    "window.STOCK_AI_CONFIG=Object.freeze("
                    + json.dumps(self.config).replace("<", "\\u003c") + ");\n",
                    media_type="text/javascript", headers={"Cache-Control": "no-store"},
                )
            else:
                response = FileResponse(self.auth_bundle, media_type="text/javascript",
                                        headers={"Cache-Control": "no-store"})
            return await response(scope, receive, send)
        if path.startswith("/api/"):
            scope = dict(scope)
            scope["path"] = path[4:]
            scope["raw_path"] = scope.get("raw_path", path.encode())[4:]
            scope["headers"] = [
                (k, v) for k, v in headers if k.lower() != b"x-stock-ai-origin"
            ] + [(b"x-stock-ai-origin", self.origin_secret.encode("ascii"))]
        await self.backend(scope, receive, send)


def compose():
    url, key = public_configuration()
    from app.settings import settings

    if settings.app_env != "development":
        raise RuntimeError("Local web runtime requires APP_ENV=development")
    bundle = build_auth_bundle()
    settings.web_auth_enabled = True
    settings.supabase_url = url
    settings.aws_origin_secret = secrets.token_urlsafe(48)
    settings.web_cors_origins = []
    settings.scheduler_enabled = False
    if settings.jobs_enabled:
        raise RuntimeError("Local web runtime requires JOBS_ENABLED=false (no local worker)")
    if settings.database_url:
        from sqlalchemy import make_url

        database = make_url(settings.database_url)
        if database.get_backend_name() == "postgresql":
            settings.database_url = database.update_query_dict(
                {"connect_timeout": "5"},
            ).render_as_string(hide_password=False)
    from app.main import app

    return LocalWebApp(app, supabase_url=url, publishable_key=key,
                       origin_secret=settings.aws_origin_secret, auth_bundle=bundle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(compose(), host="127.0.0.1", port=args.port, proxy_headers=False)


if __name__ == "__main__":
    main()
