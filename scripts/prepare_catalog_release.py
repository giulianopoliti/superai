"""Package catalog integration over HEAD without activating unrelated local work."""

import ast
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
OVERLAYS = (
    "app/assistant/tools/filters.py", "app/assistant/tools/queries.py",
    "app/assistant/tools/sales.py", "app/assistant/tools/schemas.py",
    "app/db/repositories/catalog_import_effects.py",
    "app/db/repositories/catalog_product_view.py",
    "app/db/repositories/sql_procurement.py", "app/db/repositories/sql_sales.py",
    "app/modules/catalog/product_view.py", "app/modules/catalog/resolver.py",
    "app/modules/procurement/analysis_contracts.py",
    "app/modules/procurement/catalog_interpretation.py",
    "app/modules/procurement/decision_policy.py", "app/modules/procurement/identity.py",
    "app/modules/procurement/matching.py", "app/modules/procurement/schemas.py",
    "app/modules/procurement/review.py", "app/modules/procurement/normalization.py",
    "tests/modules/procurement/test_catalog_product_integration.py",
    "tests/modules/procurement/test_document_decision_policy.py",
    "tests/auth/test_catalog_needs_migration.py",
)
CATALOG_MODELS = {
    "CatalogVocabularyModel", "CatalogInterpretationModel", "CatalogInterpretationNeedModel",
}


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def prepare():
    commit = git("rev-parse", "HEAD").decode().strip()
    files = {}
    with tarfile.open(fileobj=io.BytesIO(git("archive", "--format=tar", commit))) as archive:
        for member in archive.getmembers():
            if member.isfile() and (
                member.name.startswith(("app/", "tests/", "alembic/", "scripts/"))
                or member.name in {"pyproject.toml", "uv.lock", "README.md", "alembic.ini"}
            ):
                files[member.name] = archive.extractfile(member).read()
    models = files["app/db/models.py"].decode().replace("\r\n", "\n")
    if "    ForeignKeyConstraint,\n" not in models:
        models = models.replace("    ForeignKey,\n", "    ForeignKey,\n    ForeignKeyConstraint,\n")
    # Existing Cloud revision 0011 already created this uniqueness constraint.
    anchor = '    __tablename__ = "products"\n    __table_args__ = (\n'
    if models.count(anchor) != 1:
        raise RuntimeError("Review ProductModel transformation for this base commit")
    models = models.replace(anchor, anchor + '        UniqueConstraint("business_id", "id", '
                            'name="uq_products_business_id_id"),\n', 1)
    current = (ROOT / "app/db/models.py").read_text(encoding="utf-8")
    selected = [ast.get_source_segment(current, node) for node in ast.parse(current).body
                if isinstance(node, ast.ClassDef) and node.name in CATALOG_MODELS]
    if len(selected) != len(CATALOG_MODELS):
        raise RuntimeError("Missing reviewed catalog models")
    files["app/db/models.py"] = (models + "\n\n" + "\n\n\n".join(selected) + "\n").encode()
    overlays = list(OVERLAYS)
    # Preserve the already-applied revision graph; applying only 0018 is a separate step.
    overlays.extend(p.relative_to(ROOT).as_posix()
                    for p in (ROOT / "alembic/versions").glob("202610*.py"))
    for name in overlays:
        files[name] = (ROOT / name).read_bytes()
    forbidden = {".env", ".env.local", "__pycache__", "node_modules"}
    if any(forbidden.intersection(Path(name).parts) for name in files):
        raise RuntimeError("Private/generated file in candidate")
    release = "catalog-candidate-" + uuid4().hex[:12]
    directory = ROOT / ".data/releases" / release
    source = directory / "source"
    source.mkdir(parents=True, exist_ok=False)
    manifest = {
        "release": release, "base_commit": commit, "activation": "not activated",
        "preserved_from_head": ["app/main.py", "app/settings.py", "app/static/",
                                "pyproject.toml", "uv.lock"],
        "overlay_files": sorted(overlays), "model_classes": sorted(CATALOG_MODELS),
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())},
    }
    files["release-manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    archive_path = directory / "backend.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        for name, data in sorted(files.items()):
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(data), 0o644
            archive.addfile(member, io.BytesIO(data))
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {
        "release": release, "base_commit": commit, "archive": str(archive_path),
        "source": str(source), "file_count": len(files),
        "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
    }


if __name__ == "__main__":
    print(json.dumps(prepare()))
