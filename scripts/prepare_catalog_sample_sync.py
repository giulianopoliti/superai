"""Export validated auxiliary sample data for an atomic privileged connector import."""
# ruff: noqa: E402

import argparse
import json
import sys
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from sqlalchemy import JSON, create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.models import (
    CatalogFamilyChangeSuggestionModel as Change,
)
from app.db.models import (
    CatalogFamilyCreationSuggestionModel as Creation,
)
from app.db.models import (
    CatalogFamilySuggestionEventModel as Event,
)
from app.db.models import (
    CatalogInterpretationModel as Interpretation,
)
from app.db.models import (
    CatalogTaxonomyChangeModel as TaxonomyChange,
)
from app.db.models import (
    CatalogVocabularyModel as Vocabulary,
)
from app.db.models import (
    ProductModel,
)
from app.modules.procurement.catalog_interpretation import snapshot as source_snapshot
from scripts.prepare_catalog_family_backfill import quote


class JsonPool:
    """Deduplicate repeated audit snapshots without dropping historical evidence."""

    def __init__(self):
        self.values = {}

    def expression(self, value):
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(encoded) <= 6000:
            if len(encoded) < 160:
                return quote(encoded) + "::jsonb"
            key = sha256(encoded.encode()).hexdigest()[:24]
            if key in self.values and self.values[key] != value:
                raise ValueError("JSON payload hash collision")
            self.values[key] = value
            return "sync_payloads->" + quote(key)
        if isinstance(value, dict):
            pieces = [quote(k) + "," + self.expression(v) for k, v in value.items()]
            return (
                "("
                + " || ".join(
                    "jsonb_build_object(" + ",".join(pieces[i : i + 40]) + ")"
                    for i in range(0, len(pieces), 40)
                )
                + ")"
            )
        if isinstance(value, list):
            pieces = [self.expression(v) for v in value]
            return (
                "("
                + " || ".join(
                    "jsonb_build_array(" + ",".join(pieces[i : i + 80]) + ")"
                    for i in range(0, len(pieces), 80)
                )
                + ")"
            )
        # Long strings occur in prompts; store the value once too.
        key = sha256(encoded.encode()).hexdigest()[:24]
        self.values[key] = value
        return "sync_payloads->" + quote(key)


def literal(column, value, pool):
    if value is None:
        return "NULL"
    if isinstance(column.type, JSON):
        return pool.expression(value)
    if isinstance(value, datetime):
        return quote(value.replace(tzinfo=value.tzinfo or UTC).isoformat()) + "::timestamptz"
    if isinstance(value, bool):
        return "true" if value else "false"
    return quote(value)


def insert(row, pool):
    columns = list(row.__table__.columns)
    names = ",".join('"' + c.name + '"' for c in columns)
    values = ",".join(literal(c, getattr(row, c.name), pool) for c in columns)
    return (
        f'INSERT INTO public."{row.__tablename__}" ({names}) VALUES ({values}) '
        "ON CONFLICT (id) DO NOTHING;"
    )


def batch_sql(operations, guards):
    pool = JsonPool()
    statements = [o if isinstance(o, str) else insert(o, pool) for o in operations]
    body = (
        "DECLARE sync_payloads jsonb := "
        + quote(json.dumps(pool.values, ensure_ascii=False, separators=(",", ":")))
        + "::jsonb;\nBEGIN\n"
    )
    body += "\n".join(guards + statements) + "\nEND;\n"
    delimiter = "$stock_ai_sync_" + sha256(body.encode()).hexdigest() + "$"
    if delimiter in body:
        raise ValueError("Unexpected SQL delimiter collision")
    return "BEGIN;\nDO " + delimiter + "\n" + body + delimiter + ";\nCOMMIT;\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    original = json.loads(args.snapshot.read_text(encoding="utf-8"))
    report = json.loads(args.report.read_text(encoding="utf-8"))
    business_id = original["business_id"]
    expected_ids = {p["id"] for p in original["products"]}
    if (
        report["business_id"] != business_id
        or len(report["results"]) != 100
        or {r["product_id"] for r in report["results"]} != expected_ids
    ):
        raise ValueError("A complete matching sample is required")
    b = quote(business_id)
    guards = [
        f"PERFORM id FROM businesses WHERE id={b} FOR UPDATE;",
        "IF NOT FOUND THEN RAISE EXCEPTION 'Business missing'; END IF;",
        f"PERFORM id FROM products WHERE business_id={b} FOR SHARE;",
        f"PERFORM id FROM product_families WHERE business_id={b} FOR SHARE;",
        f"PERFORM id FROM catalog_vocabulary WHERE business_id={b} FOR SHARE;",
        f"IF (SELECT md5(string_agg(row_to_json(p)::text,E'\\n' order by id)) "
        f"FROM products p WHERE business_id={b}) IS DISTINCT FROM "
        f"{quote(original['fingerprint'])} THEN "
        "RAISE EXCEPTION 'Catalog changed since export'; END IF;",
    ]
    statements, operations, counts, pool = [], [], {}, JsonPool()
    with Session(create_engine("sqlite:///" + args.database.resolve().as_posix())) as session:
        current = {
            p.id: source_snapshot(p)
            for p in session.scalars(
                select(ProductModel).where(ProductModel.business_id == business_id)
            )
        }
        if any(current.get(r["product_id"]) != r["source"] for r in report["results"]):
            raise ValueError("Source data changed during processing")
        for family in original["families"]:
            keys = ("name", "normalized_name", "external_family_id", "active")
            expected = {k: family[k] for k in keys}
            expr = ",".join(f"{quote(k)},{k}" for k in keys)
            guards.append(
                f"IF NOT EXISTS (SELECT 1 FROM product_families WHERE business_id={b} "
                f"AND id={quote(family['id'])} AND jsonb_build_object({expr})="
                f"{quote(json.dumps(expected))}::jsonb) THEN "
                "RAISE EXCEPTION 'Family changed since export'; END IF;"
            )
        for old in original["vocabulary"]:
            node = session.get(Vocabulary, old["id"])
            keys = ("attribute", "label", "normalized_label", "aliases", "parent_id", "status")
            before = {k: old[k] for k in keys}
            after = {k: getattr(node, k) for k in keys}
            if before != after:
                if (
                    {k: v for k, v in before.items() if k != "status"}
                    != {k: v for k, v in after.items() if k != "status"}
                    or before["status"] != "proposed"
                    or after["status"] != "active"
                ):
                    raise ValueError("Unexpected mutation of existing vocabulary")
                statements.append(
                    f"UPDATE catalog_vocabulary SET status='active' "
                    f"WHERE business_id={b} AND id={quote(node.id)};"
                )
                operations.append(statements[-1])
            expr = ",".join(f"{quote(k)},{k}" for k in keys)
            guards.append(
                f"IF NOT EXISTS (SELECT 1 FROM catalog_vocabulary WHERE business_id={b} "
                f"AND id={quote(node.id)} AND jsonb_build_object({expr}) IN "
                f"({quote(json.dumps(before))}::jsonb,{quote(json.dumps(after))}::jsonb)) THEN "
                "RAISE EXCEPTION 'Vocabulary changed since export'; END IF;"
            )
        for model, key in (
            (Vocabulary, "vocabulary"),
            (TaxonomyChange, None),
            (Interpretation, "interpretations"),
            (Creation, "creations"),
            (Change, "changes"),
            (Event, "events"),
        ):
            old_ids = {r["id"] for r in original[key]} if key else set()
            rows = list(session.scalars(select(model).where(model.business_id == business_id)))
            if model is Vocabulary:
                rows.sort(key=lambda r: bool(r.parent_id))
            # Keep superseded attempts too: audit snapshots refer to their IDs.
            new_rows = [r for r in rows if r.id not in old_ids]
            if model in {Change, Creation} and any(r.status != "pending" for r in new_rows):
                raise ValueError("Only pending review proposals may be inserted")
            statements.extend(insert(r, pool) for r in new_rows)
            operations.extend(new_rows)
            counts[model.__tablename__] = len(new_rows)
    body = (
        "DECLARE sync_payloads jsonb := "
        + quote(json.dumps(pool.values, ensure_ascii=False, separators=(",", ":")))
        + "::jsonb;\nBEGIN\n"
    )
    body += "\n".join(guards + statements) + "\nEND;\n"
    delimiter = "$stock_ai_sync_" + sha256(body.encode()).hexdigest() + "$"
    if delimiter in body:
        raise ValueError("Unexpected SQL delimiter collision")
    sql = "BEGIN;\nDO " + delimiter + "\n" + body + delimiter + ";\nCOMMIT;\n"
    args.output.write_text(sql, encoding="utf-8")
    batches, pending = [], []
    for operation in operations:
        candidate = batch_sql(pending + [operation], guards)
        if len(candidate.encode()) > 650000 and pending:
            batches.append(batch_sql(pending, guards))
            pending = [operation]
        else:
            pending.append(operation)
    if pending:
        batches.append(batch_sql(pending, guards))
    batch_manifest = []
    for index, batch in enumerate(batches):
        if len(batch.encode()) > 950000:
            raise ValueError("Single operation exceeds connector transport budget")
        prefix = args.output.with_suffix(f".batch-{index:03}")
        parts = [batch[i : i + 30000] for i in range(0, len(batch), 30000)]
        paths = []
        for part_index, part in enumerate(parts):
            target = Path(str(prefix) + f".part-{part_index:03}.txt")
            target.write_text(part, encoding="utf-8")
            paths.append(target.as_posix())
        batch_manifest.append({"characters": len(batch), "parts": paths})
    # Chunks are only a client transport aid. Join all before executing atomically.
    chunks = [sql[i : i + 30000] for i in range(0, len(sql), 30000)]
    for index, chunk in enumerate(chunks):
        args.output.with_suffix(f".chunk-{index:03}.txt").write_text(chunk, encoding="utf-8")
    metadata = {
        "business_id": business_id,
        "counts": counts,
        "characters": len(sql),
        "chunks": len(chunks),
        "original_fingerprint": original["fingerprint"],
        "batches": batch_manifest,
    }
    args.output.with_suffix(".metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    print(json.dumps(metadata))


if __name__ == "__main__":
    main()
