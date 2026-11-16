"""Capture UI hashes and route inventory without importing the app or reading .env.

Optional remote checks GET only the four public static assets. No business requests,
uploads, provider calls or server changes are performed.
"""

import argparse
import ast
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ("index.html", "styles.css", "app.js", "super-salvador.png")


def hashes(body: bytes, name: str) -> dict:
    result = {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
    if name.endswith((".html", ".css", ".js")):
        # Git checkouts can use CRLF while the Linux deployment uses LF.
        canonical = body.replace(b"\r\n", b"\n")
        result["lf_sha256"] = hashlib.sha256(canonical).hexdigest()
    return result


def route_inventory() -> list[dict]:
    routes = []
    tree = ast.parse((ROOT / "app/main.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            method = decorator.func
            if not (isinstance(method, ast.Attribute)
                    and isinstance(method.value, ast.Name) and method.value.id == "api"
                    and method.attr in {"get", "post", "put", "patch", "delete"}):
                continue
            path = ast.literal_eval(decorator.args[0])
            scope = "web_business"
            if path == "/health":
                scope = "health"
            elif path.startswith("/internal/"):
                scope = "service"
            elif path.startswith("/webhooks/"):
                scope = "webhook"
            elif path.startswith("/ui/") or path == "/procurement-ui":
                scope = "static"
            routes.append({"method": method.attr.upper(), "path": path,
                           "handler": node.name, "line": decorator.lineno, "scope": scope})
    return sorted(routes, key=lambda row: (row["path"], row["method"]))


def remote_asset(origin: str, name: str, local: dict) -> dict:
    started = monotonic()
    try:
        request = Request(f"{origin}/ui/{name}", headers={"Cache-Control": "no-cache"})
        with urlopen(request, timeout=12) as response:
            body = response.read(8 * 1024 * 1024 + 1)
            if len(body) > 8 * 1024 * 1024:
                return {"error": "Static asset exceeds 8 MiB capture limit"}
            observed = hashes(body, name)
            key = "lf_sha256" if "lf_sha256" in local else "sha256"
            return {**observed, "status": response.status,
                    "final_url": response.url,
                    "content_type": response.headers.get("Content-Type"),
                    "matches_local": observed[key] == local[key],
                    "elapsed_seconds": round(monotonic() - started, 3)}
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        # Never record response bodies, cookies or arbitrary headers.
        return {"error_type": type(error).__name__,
                "elapsed_seconds": round(monotonic() - started, 3)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--origin", help="Explicit existing HTTP(S) origin; static GETs only")
    args = parser.parse_args()
    origin = args.origin.rstrip("/") if args.origin else None
    if origin:
        parsed = urlsplit(origin)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.path or parsed.query
                or parsed.fragment):
            parser.error("--origin must be an HTTP(S) origin without credentials or path")
    assets = {name: hashes((ROOT / "app/static" / name).read_bytes(), name) for name in ASSETS}
    result = {
        "captured_at": datetime.now(UTC).isoformat(),
        "local_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "tracked_changes": subprocess.check_output(
            ["git", "diff", "--name-only", "HEAD"], cwd=ROOT, text=True).splitlines(),
        "assets": assets,
        "routes": route_inventory(),
        "route_inventory_limit": (
            "AST of app/main.py decorators; review mounts and new routers manually"
        ),
        "remote_origin": origin,
        "remote_commit": None,
        "remote_assets": ({name: remote_asset(origin, name, local)
                           for name, local in assets.items()} if origin else {}),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Captured {len(assets)} assets and {len(result['routes'])} routes in {args.output}")
    for name, observed in result["remote_assets"].items():
        print(f"{name}: {observed.get('error_type', observed.get('matches_local'))}")


if __name__ == "__main__":
    main()
