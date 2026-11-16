"""Run the exact approved sample from a verified DB export when direct SQL is unavailable."""
# ruff: noqa: E402

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.models import (
    Base,
    BusinessModel,
    CatalogFamilyChangeSuggestionModel,
    CatalogFamilyCreationSuggestionModel,
    CatalogFamilySuggestionEventModel,
    CatalogInterpretationModel,
    CatalogVocabularyModel,
    ProductFamilyModel,
    ProductModel,
)
from app.modules.procurement.catalog_interpretation import snapshot as product_snapshot
from app.settings import settings
from scripts.interpret_catalog_full import run_full
from scripts.prepare_catalog_family_backfill import restore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    exported = json.loads(args.snapshot.read_text(encoding="utf-8"))
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    business_id = exported["business_id"]
    if baseline["business_id"] != business_id:
        raise ValueError("Business mismatch")
    selected = {r["product_id"]: r["source"] for r in baseline["results"]}
    if len(selected) != 100 or set(selected) != {p["id"] for p in exported["products"]}:
        raise ValueError("The export must contain the same 100 products")
    url = "sqlite:///" + args.database.resolve().as_posix()
    fresh = not args.database.exists()
    engine = create_engine(url)
    if fresh:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(BusinessModel(id=business_id, name="Verified sample"))
            session.flush()
            for model, key in (
                (ProductFamilyModel, "families"),
                (ProductModel, "products"),
                (CatalogVocabularyModel, "vocabulary"),
                (CatalogInterpretationModel, "interpretations"),
                (CatalogFamilyCreationSuggestionModel, "creations"),
                (CatalogFamilyChangeSuggestionModel, "changes"),
                (CatalogFamilySuggestionEventModel, "events"),
            ):
                if any(r["business_id"] != business_id for r in exported[key]):
                    raise ValueError("Export mixes businesses")
                session.add_all(restore(model, r) for r in exported[key])
                session.flush()
            session.commit()
    with Session(engine) as session:
        current = {
            p.id: product_snapshot(p)
            for p in session.scalars(
                select(ProductModel).where(ProductModel.business_id == business_id)
            )
        }
        if current != selected:
            raise ValueError("Source products changed since the baseline")
    settings.database_url = url
    args.output.parent.mkdir(parents=True, exist_ok=True)
    segment = args.output.with_name(args.output.stem + ".segment-" + uuid4().hex[:8] + ".json")
    report = run_full(
        argparse.Namespace(
            business_id=business_id,
            product_id=list(selected),
            limit=100,
            model="gpt-6-luna",
            threshold=0.9,
            reorganize=False,
            reuse_proposals=None,
            output=segment,
        )
    )
    segments = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in args.output.parent.glob(args.output.stem + ".segment-*.json")
    ]
    segments = [s for s in segments if s.get("config_hash") == report["config_hash"]]
    for key in (
        "api_calls",
        "decisions_calls",
        "generative_calls",
        "input_tokens",
        "output_tokens",
        "estimated_cost_usd",
    ):
        report[key] = sum(s.get(key, 0) for s in segments)
    report["estimated_cost_incomplete"] = any(s.get("estimated_cost_incomplete") for s in segments)
    report["segment_run_ids"] = [s["run_id"] for s in segments]
    report["sample"] = {
        "size": 100,
        "baseline": str(args.baseline),
        "db_fingerprint": exported["fingerprint"],
    }
    report["comparison"] = {
        key: {
            "before": sum(bool(r.get(key)) for r in baseline["results"]),
            "after": sum(bool(r.get(key)) for r in report["results"]),
        }
        for key in ("category", "subcategory", "brand", "variant")
    }
    with Session(engine) as session:
        report["original_sample_unchanged"] = current == {
            p.id: product_snapshot(p)
            for p in session.scalars(
                select(ProductModel).where(ProductModel.business_id == business_id)
            )
        }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "results": len(report["results"]),
                "errors": report["errors"],
                "comparison": report["comparison"],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
