import hashlib
import json
import tarfile
from pathlib import Path

import pytest

from scripts import prepare_web_release as release


def put(root, name, data=b"source"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_workspace_snapshot_keeps_current_dependencies_and_excludes_private_data(tmp_path):
    for name in [
        "app/main.py", "app/db/models.py", "app/modules/procurement/page_analysis.py",
        "app/assistant/prompts/offer_group_membership_v1.md", "tests/fixtures/case.json",
        "frontend/auth-entry.mjs", "infra/nginx/stock-ai.conf", "docs/PLAN.md",
        "app/static/app.js", "app/static/super-salvador.png", "pyproject.toml",
    ]:
        put(tmp_path, name, b"current-workspace")
    private = [
        ".env", ".data/releases/private.json", "app/static/customer.csv",
        "app/static/secret.env", "app/static/upload.pdf", "app/__pycache__/main.pyc",
        "app/.hidden/secret.py", "tests/fixtures/customer.pdf", "docs/baselines/real.json",
        "node_modules/package/index.js", "output/diagnostics/private.json", "key.pem",
    ]
    for name in private:
        put(tmp_path, name, b"private-canary")
    files = release.collect_release_files(tmp_path)
    assert files["app/db/models.py"] == b"current-workspace"
    assert "app/modules/procurement/page_analysis.py" in files
    assert "app/assistant/prompts/offer_group_membership_v1.md" in files
    assert "tests/fixtures/case.json" in files
    assert not set(private).intersection(files)
    assert all(b"private-canary" not in data for data in files.values())


def test_archive_is_complete_and_manifest_matches_current_bytes(tmp_path, monkeypatch):
    required = {"app/main.py", "app/db/models.py", "pyproject.toml"}
    monkeypatch.setattr(release, "REQUIRED", required)
    for name in required:
        put(tmp_path, name, (name + " latest").encode())
    result = release.write_release(tmp_path, tmp_path / ".data/releases", base_commit="base")
    with tarfile.open(result["archive"]) as archive:
        members = {member.name: archive.extractfile(member).read() for member in archive}
    manifest = json.loads(members.pop("release-manifest.json"))
    assert set(members) == required
    assert manifest["expected_schema"] == "20261030_0019"
    assert manifest["activation"].startswith("not activated")
    for name, data in members.items():
        assert data == (tmp_path / name).read_bytes()
        assert manifest["files"][name] == hashlib.sha256(data).hexdigest()
    assert result["sha256"] == hashlib.sha256(Path(result["archive"]).read_bytes()).hexdigest()


def test_incomplete_candidate_is_rejected_before_archive(tmp_path):
    put(tmp_path, "app/main.py")
    with pytest.raises(RuntimeError, match="Incomplete workspace"):
        release.write_release(tmp_path, tmp_path / "releases", base_commit="base")
    assert not (tmp_path / "releases").exists()
