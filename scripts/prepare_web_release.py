"""Package the complete reviewed workspace, without secrets or customer data.

This only creates a private candidate. It never changes a service, installs
dependencies, runs migrations, or activates the candidate.
"""

import argparse
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {
    "pyproject.toml", "uv.lock", "README.md", "alembic.ini", "package.json",
    "package-lock.json", "vercel.json", ".vercelignore", "Dockerfile", "docker-compose.yml",
}
STATIC_FILES = {
    "index.html", "app.js", "styles.css", "super-salvador.png", "config.js",
    "transport.js", "jobs.js",
}
DIRECTORIES = {
    "app": {".py"},
    "tests": {".py", ".cjs"},
    "alembic": {".py", ".mako"},
    "scripts": {".py", ".cjs"},
    "frontend": {".mjs"},
    "infra": {".conf", ".service", ".timer"},
    "docs": {".md"},
}
REQUIRED = {
    "app/main.py", "app/settings.py", "app/db/models.py", "app/assistant/engine.py",
    "app/auth/http.py", "app/jobs/worker.py", "app/modules/procurement/page_analysis.py",
    "app/modules/procurement/analysis_pipeline.py", "app/modules/catalog/product_view.py",
    "app/providers/jev_offer_identity.py", "app/static/index.html",
    "frontend/auth-entry.mjs", "scripts/build_frontend.cjs", "tests/test_api.py",
    "alembic/versions/20261021_0018_catalog_interpretation_needs.py",
    "alembic/versions/20261030_0019_product_analysis_preferences.py",
    "app/db/repositories/product_analysis_preferences.py",
    "app/modules/catalog/analysis_preferences.py",
    "app/db/repositories/document_summary_projection.py", "tests/test_home_performance.py",
    "tests/frontend/home_loading.test.cjs",
    "pyproject.toml", "uv.lock",
}


def collect_release_files(root: Path) -> dict[str, bytes]:
    """Explicit source allowlist; never merge current entrypoints into old HEAD."""
    root = root.resolve()
    files = {}
    candidates = [root / name for name in ROOT_FILES]
    for name in DIRECTORIES:
        candidates.extend((root / name).rglob("*"))
    for path in sorted(candidates):
        name = path.relative_to(root).as_posix()
        relative_parts = path.parts[len(root.parts):]
        if any(part.startswith(".") or part == "__pycache__" for part in relative_parts):
            if name not in ROOT_FILES:
                continue
        if path.is_symlink():
            raise RuntimeError(f"Release source must not be a symbolic link: {name}")
        if not path.is_file():
            continue
        if not path.resolve().is_relative_to(root):
            raise RuntimeError(f"Release source escapes workspace: {name}")
        allowed = name in ROOT_FILES
        if name.startswith("app/static/"):
            allowed = name.removeprefix("app/static/") in STATIC_FILES
        elif name.startswith("app/assistant/prompts/"):
            allowed = path.suffix in {".md", ".json"}
        elif name.startswith("tests/fixtures/"):
            allowed = path.suffix == ".json"
        elif not allowed:
            directory = name.split("/", 1)[0]
            allowed = path.suffix in DIRECTORIES.get(directory, set())
        if allowed:
            files[name] = path.read_bytes()
    return files


def write_release(root: Path, output: Path, *, base_commit: str) -> dict:
    files = collect_release_files(root)
    missing = REQUIRED - files.keys()
    if missing:
        raise RuntimeError("Incomplete workspace candidate: " + ", ".join(sorted(missing)))
    release = "web-candidate-" + uuid4().hex[:12]
    directory = output / release
    directory.mkdir(parents=True, exist_ok=False)
    manifest = {
        "release": release, "base_commit": base_commit,
        "source": "complete allowlisted workspace snapshot; includes uncommitted source",
        "activation": "not activated; isolated candidate only",
        "expected_schema": "20261030_0019",
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())},
    }
    files["release-manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    archive_path = directory / "backend.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        for name, data in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(data), 0o644
            archive.addfile(member, io.BytesIO(data))
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {
        "release": release, "base_commit": base_commit, "archive": str(archive_path),
        "file_count": len(files), "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / ".data/releases")
    args = parser.parse_args()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    print(json.dumps(write_release(ROOT, args.output_dir.resolve(), base_commit=commit)))


if __name__ == "__main__":
    main()
