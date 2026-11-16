"""Resumable bounded-concurrency enrichment; never updates source products."""
# ruff: noqa: E402

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, or_, select, text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.models import CatalogInterpretationModel, ProductModel
from app.modules.catalog.product_view import COMPATIBLE_PROMPT_HEADERS
from app.modules.procurement.catalog_interpretation import digest, snapshot
from app.settings import settings
from scripts.interpret_catalog_full import run_full


def save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--business-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=6, choices=range(1, 25))
    parser.add_argument("--batch-size", type=int, default=30)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 100:
        raise ValueError("Invalid batch size")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        settings.database_url,
        pool_size=min(3,args.workers),
        max_overflow=0,
        pool_timeout=120,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 10},
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    fingerprint_sql = text(
        "select md5(string_agg(row_to_json(p)::text,E'\\n' order by id)) "
        "from products p where business_id=:business_id"
    )
    with factory() as session:
        products = session.scalars(
            select(ProductModel)
            .where(ProductModel.business_id == args.business_id, ProductModel.active.is_(True))
            .order_by(ProductModel.name, ProductModel.id)
        ).all()
        sources = {p.id: snapshot(p) for p in products}
        prior = session.execute(
            select(
                CatalogInterpretationModel.product_id, CatalogInterpretationModel.source_hash
            ).where(
                CatalogInterpretationModel.business_id == args.business_id,
                or_(*(CatalogInterpretationModel.request["prompt"].as_string().like(header + "\n%")
                      for header in COMPATIBLE_PROMPT_HEADERS)),
            )
        ).all()
        fresh = {
            identifier
            for identifier, h in prior
            if identifier in sources and digest(sources[identifier]) == h
        }
        before = session.scalar(fingerprint_sql, {"business_id": args.business_id})
    remaining = [p for p in sources if p not in fresh]
    if args.limit is not None:
        remaining = remaining[: args.limit]
    progress = {
        "business_id": args.business_id,
        "started_at": datetime.now(UTC).isoformat(),
        "state": "running",
        "active_products": len(sources),
        "preserved_fresh": len(fresh),
        "selected": len(remaining),
        "completed": 0,
        "failed": [],
        "estimated_cost_usd": 0,
        "estimated_cost_incomplete": False,
        "api_calls": 0,
        "summary": {},
        "reports": [],
        "source_fingerprint_before": before,
    }
    manifest_path = args.output_dir / "progress.json"
    save(manifest_path, progress)
    save(
        args.output_dir / "selection.json",
        {
            "business_id": args.business_id,
            "source_fingerprint": before,
            "products": [sources[p] for p in remaining],
        },
    )
    print(
        json.dumps({k: progress[k] for k in ("active_products", "preserved_fresh", "selected")}),
        flush=True,
    )

    def process(ids):
        output = args.output_dir / ("batch-" + uuid4().hex + ".json")
        report = run_full(
            argparse.Namespace(
                business_id=args.business_id,
                product_id=ids,
                limit=len(ids),
                model="gpt-6-luna",
                threshold=0.9,
                output=output,
                compact_audit=True,
                session_factory=factory,
            )
        )
        return ids, output, report

    queue = [remaining[i : i + args.batch_size] for i in range(0, len(remaining), args.batch_size)]
    totals = Counter()
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        pending = {executor.submit(process, ids) for ids in queue}
        while pending:
            future = next(as_completed(pending))
            pending.remove(future)
            try:
                ids, output, report = future.result()
            except Exception as exc:
                progress["failed"].append({"error": type(exc).__name__, "scope": "batch_start"})
                save(manifest_path, progress)
                continue
            done = {r["product_id"] for r in report["results"]}
            failed = {e["product_id"] for e in report["errors"]}
            progress["completed"] += len(done)
            progress["failed"].extend(report["errors"])
            progress["reports"].append(str(output))
            for key in ("estimated_cost_usd", "api_calls"):
                progress[key] += report[key]
            progress["estimated_cost_incomplete"] |= report["estimated_cost_incomplete"]
            for row in report["results"]:
                for key in (
                    "category",
                    "subcategory",
                    "brand",
                    "content_value",
                    "packaging",
                    "variant",
                ):
                    totals[key] += bool(row.get(key))
                totals["brand_and_content"] += bool(row.get("brand") and row.get("content_value"))
                totals["family_" + row["response"]["original_family_validation"]["status"]] += 1
            progress["summary"] = dict(totals)
            save(manifest_path, progress)
            print(
                json.dumps(
                    {
                        "completed": progress["completed"],
                        "selected": len(remaining),
                        "failed": len(progress["failed"]),
                    }
                ),
                flush=True,
            )
            # The failed item is preserved for review; continue its unattempted siblings.
            unattempted = [identifier for identifier in ids if identifier not in done | failed]
            if unattempted and (done or failed):
                pending.add(executor.submit(process, unattempted))
    with factory() as session:
        after = session.scalar(fingerprint_sql, {"business_id": args.business_id})
    progress.update(
        state="completed" if not progress["failed"] else "completed_with_errors",
        finished_at=datetime.now(UTC).isoformat(),
        source_fingerprint_after=after,
        original_catalog_unchanged=before == after,
    )
    save(manifest_path, progress)
    engine.dispose()
    print(json.dumps(progress, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
