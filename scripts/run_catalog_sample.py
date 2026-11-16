"""Reproducible, family-stratified catalog pilot with deliberate difficult cases."""
# ruff: noqa: E402

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from app.db.models import CatalogInterpretationModel, ProductModel
from app.db.session import SessionLocal
from app.modules.procurement.catalog_interpretation import snapshot
from scripts.interpret_catalog import export_row, run
from scripts.interpret_catalog_full import configuration_hash


def choose_sample(products, *, size=100, seed="catalog-2026-10-13"):
    groups = defaultdict(list)
    for product in products:
        groups[product.category or "SIN FAMILIA"].append(product)
    if size < len(groups) or size > len(products):
        raise ValueError("Sample must cover every family and fit the available catalog")

    def rank(product):
        return hashlib.sha256(f"{seed}/{product.id}".encode()).hexdigest()

    # Avoid repeatedly sampling identical placeholder names within the same family.
    unique = {}
    for family, rows in groups.items():
        by_name = {}
        for row in sorted(rows, key=rank):
            by_name.setdefault(row.name.strip().casefold(), row)
        unique[family] = list(by_name.values())
    quotas = {family: min(2, len(rows)) for family, rows in unique.items()}
    if "ALIMENTO PARA ANIMALES" in quotas:
        quotas["ALIMENTO PARA ANIMALES"] = min(12, len(unique["ALIMENTO PARA ANIMALES"]))
    if sum(quotas.values()) > size:
        quotas = {family: 1 for family in quotas}
    while sum(quotas.values()) < size:
        available = [f for f in quotas if quotas[f] < len(unique[f])]
        if not available:
            raise ValueError("Not enough distinct names to complete the sample")
        family = max(available, key=lambda f: (len(groups[f]) / (quotas[f] + 1), f))
        quotas[family] += 1
    selected = []
    preferred = (
        "ASD",
        "CAFE EN SAQUITOS",
        "CIF DETERGENTE",
        "LECHE LA SERENISIMA",
        "MAIZENA",
        "GENIOL TAFIROL",
        "CAT CHOW",
        "DOG CHOW",
    )
    for family in sorted(unique):
        rows = sorted(unique[family], key=rank)
        chosen = []
        if family == "ALIMENTO PARA ANIMALES":
            for prefix in preferred:
                matches = [r for r in rows if r.name.upper().startswith(prefix)]
                if matches and matches[0] not in chosen:
                    chosen.append(matches[0])
        if not chosen:
            short = sorted(rows, key=lambda r: (len(r.name.split()), rank(r)))
            chosen = short[: min(2, quotas[family])]
        chosen += [r for r in rows if r not in chosen][: max(0, quotas[family] - len(chosen))]
        selected.extend(chosen[: quotas[family]])
    assert len(selected) == size and len({p.id for p in selected}) == size
    return selected


def main():
    parser = argparse.ArgumentParser(
        description="Procesar una muestra variada de productos desde DB"
    )
    parser.add_argument("--business-id", required=True)
    parser.add_argument("--size", type=int, default=100)
    parser.add_argument("--seed", default="catalog-2026-10-13")
    parser.add_argument("--reuse-proposals", type=Path)
    parser.add_argument(
        "--output", type=Path, default=Path("output/catalog/sample-100-2026-10-13.json")
    )
    args = parser.parse_args()
    if not 1 <= args.size <= 100 or SessionLocal is None:
        parser.error(
            "La muestra debe tener hasta 100 productos y DATABASE_URL debe estar configurada"
        )
    with SessionLocal() as session:
        all_rows = (
            session.execute(
                select(ProductModel.__table__)
                .where(ProductModel.business_id == args.business_id)
                .order_by(ProductModel.id)
            )
            .mappings()
            .all()
        )
        before_hash = hashlib.sha256(
            json.dumps([dict(r) for r in all_rows], sort_keys=True, default=str).encode()
        ).hexdigest()
        products = session.scalars(
            select(ProductModel).where(
                ProductModel.business_id == args.business_id, ProductModel.active.is_(True)
            )
        ).all()
        sample = choose_sample(products, size=args.size, seed=args.seed)
        manifest = {
            "business_id": args.business_id,
            "seed": args.seed,
            "method": "Cuotas por familia y casos deliberados; muestra no representativa",
            "original_catalog_sha256": before_hash,
            "original_catalog_count": len(all_rows),
            "active_catalog_count": len(products),
            "family_counts": dict(Counter(p.category or "SIN FAMILIA" for p in sample)),
            "products": [snapshot(p) for p in sample],
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.with_suffix(".selection.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {"selected": len(sample), "families": manifest["family_counts"]}, ensure_ascii=False
        ),
        flush=True,
    )
    previous_reports = []
    with SessionLocal() as session:
        families = sorted(
            {
                f
                for f in session.scalars(
                    select(ProductModel.category).where(
                        ProductModel.business_id == args.business_id
                    )
                ).all()
                if f
            }
        )
    expected_config = configuration_hash("gpt-6-luna", 0.9, families)
    if args.output.exists():
        previous = json.loads(args.output.read_text(encoding="utf-8"))
        if previous.get("business_id") != args.business_id:
            raise ValueError("Previous report belongs to another business")
        if previous.get("config_hash") == expected_config:
            previous_reports.append(previous)
    seen_runs = (
        set(previous_reports[0].get("segment_run_ids", [previous_reports[0]["run_id"]]))
        if previous_reports
        else set()
    )
    for segment_path in sorted(args.output.parent.glob(args.output.stem + ".segment-*.json")):
        segment = json.loads(segment_path.read_text(encoding="utf-8"))
        if (
            segment["run_id"] not in seen_runs
            and segment.get("business_id") == args.business_id
            and segment.get("config_hash") == expected_config
        ):
            previous_reports.append(segment)
            seen_runs.add(segment["run_id"])
    results_by_product = {
        r["product_id"]: r for previous in previous_reports for r in previous["results"]
    }
    sources_by_id = {p.id: snapshot(p) for p in sample}
    results_by_product = {
        identifier: result
        for identifier, result in results_by_product.items()
        if identifier in sources_by_id and result["source"] == sources_by_id[identifier]
    }
    remaining = [p.id for p in sample if p.id not in results_by_product]
    segment_output = args.output.with_name(args.output.stem + f".segment-{uuid4().hex[:8]}.json")
    print(f"Reanudar: {len(results_by_product)} guardados; {len(remaining)} pendientes", flush=True)
    report = (
        run(
            argparse.Namespace(
                business_id=args.business_id,
                limit=len(remaining),
                product_id=remaining,
                model="gpt-6-luna",
                threshold=0.9,
                vocabulary=None,
                reorganize=False,
                reuse_proposals=args.reuse_proposals,
                output=segment_output,
            )
        )
        if remaining
        else dict(previous_reports[0])
    )
    if remaining:
        for previous in previous_reports:
            for key in (
                "api_calls",
                "input_tokens",
                "output_tokens",
                "decisions_calls",
                "generative_calls",
                "estimated_cost_usd",
            ):
                report[key] += previous.get(key, 0)
            report["estimated_cost_incomplete"] |= previous.get("estimated_cost_incomplete", False)
        new_errors = list(report["errors"])
        report["attempt_errors"] = [
            error for old in previous_reports for error in old.get("errors", [])
        ]
        results_by_product.update({r["product_id"]: r for r in report["results"]})
        report["results"] = [results_by_product[p.id] for p in sample if p.id in results_by_product]
        report["errors"] = new_errors
        report["segment_run_ids"] = list(
            seen_runs | {r["run_id"] for r in previous_reports} | {report["run_id"]}
        )
    report["selected_products"] = args.size
    report["persisted_or_reused"] = len(report["results"])
    # Automatic vocabulary edits can refresh prior interpretations during the batch.
    with SessionLocal() as session:
        for index, result in enumerate(report["results"]):
            row = session.scalar(
                select(CatalogInterpretationModel).where(
                    CatalogInterpretationModel.id == result["id"],
                    CatalogInterpretationModel.business_id == args.business_id,
                )
            )
            report["results"][index] = {**result, **export_row(row, cached=result["cached"])}
        after_rows = (
            session.execute(
                select(ProductModel.__table__)
                .where(ProductModel.business_id == args.business_id)
                .order_by(ProductModel.id)
            )
            .mappings()
            .all()
        )
        after_hash = hashlib.sha256(
            json.dumps([dict(r) for r in after_rows], sort_keys=True, default=str).encode()
        ).hexdigest()
    report["sample"] = manifest
    report["original_catalog_unchanged"] = before_hash == after_hash
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "completed": len(report["results"]),
                "errors": report["errors"],
                "cost_usd": report["estimated_cost_usd"],
                "original_catalog_unchanged": report["original_catalog_unchanged"],
            }
        ),
        flush=True,
    )
    if report["errors"] or len(report["results"]) != args.size:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
