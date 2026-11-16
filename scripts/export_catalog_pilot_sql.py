"""Export guarded pilot inserts for the Supabase connector when PostgreSQL is unreachable."""
# ruff: noqa: E402

import argparse
import json
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.catalog.product_view import COMPATIBLE_PROMPT_HEADERS
from app.modules.procurement.catalog_decisions_first import PROMPT_HEADER
from app.modules.procurement.catalog_interpretation import digest

TYPES = {
    "id": "text",
    "run_id": "text",
    "business_id": "text",
    "product_id": "text",
    "source_hash": "text",
    "config_hash": "text",
    "source": "jsonb",
    "product_type": "text",
    "category": "text",
    "subcategory": "text",
    "brand": "text",
    "line": "text",
    "variant": "text",
    "packaging": "text",
    "content_value": "numeric",
    "content_unit": "text",
    "package_quantity": "integer",
    "minimum_order_quantity": "integer",
    "standard_name": "text",
    "status": "text",
    "attributes": "jsonb",
    "request": "jsonb",
    "response": "jsonb",
}


def guarded_sql(rows, business_id):
    if not rows or len({r["product_id"] for r in rows}) != len(rows):
        raise ValueError("Empty or duplicate product batch")
    compact = []
    for row in rows:
        if (
            row["business_id"] != business_id
            or row["source"]["business_id"] != business_id
            or row["source"]["product_id"] != row["product_id"]
            or row["source_hash"] != digest(row["source"])
            or not row["request"]["prompt"].startswith(PROMPT_HEADER + "\n")
        ):
            raise ValueError("Invalid tenant, source or interpretation contract")
        item = {key: row.get(key) for key in TYPES}
        # Full candidates and raw responses remain in the durable local report/ledger.
        # Cloud retains their digest, selection evidence, counts and exact snapshot.
        request = row["request"]
        item["request"] = {
            "prompt": request["prompt"],
            "policy": request["policy"],
            "candidate_digest": digest(request["candidates"]),
            "candidate_counts": {k: len(v) for k, v in request["candidates"].items()},
            "candidate_audit": request["candidate_audit"],
        }
        compact.append(item)
    payload = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
    body_tag = "$pilot_" + uuid4().hex + "$"
    data_tag = "$data_" + uuid4().hex + "$"
    assert body_tag not in payload and data_tag not in payload
    declaration = ", ".join(f"{key} {kind}" for key, kind in TYPES.items())
    columns = ", ".join(TYPES)
    selected = ", ".join("r." + key for key in TYPES)
    fresh = " OR ".join(
        "i.request->>'prompt' LIKE '" + h + "\n%'" for h in COMPATIBLE_PROMPT_HEADERS
    )
    return f"""DO {body_tag}
DECLARE payload jsonb := {data_tag}{payload}{data_tag}::jsonb;
matched integer;
BEGIN
PERFORM p.id FROM products p JOIN jsonb_to_recordset(payload) AS r({declaration})
 ON p.id=r.product_id AND p.business_id=r.business_id FOR UPDATE OF p;
SELECT count(*) INTO matched FROM products p
 JOIN jsonb_to_recordset(payload) AS r({declaration})
 ON p.id=r.product_id AND p.business_id=r.business_id
 WHERE p.active AND r.source=jsonb_build_object(
 'product_id',p.id,'business_id',p.business_id,'original_name',p.name,
 'external_product_id',p.external_product_id,'brand',p.brand,'category',p.category,
 'unit_size',p.unit_size::text,'unit',p.unit);
IF matched <> jsonb_array_length(payload) THEN
 RAISE EXCEPTION 'Product changed or tenant mismatch; pilot batch not saved';
END IF;
IF EXISTS (SELECT 1 FROM jsonb_to_recordset(payload) AS r({declaration}),
 jsonb_each(r.attributes) AS a(key,value)
 WHERE a.key <> 'product_type' AND a.value->>'status'='interpreted'
 AND a.value->>'vocabulary_id' IS NOT NULL AND NOT EXISTS (
 SELECT 1 FROM catalog_vocabulary v WHERE v.id=a.value->>'vocabulary_id'
 AND v.business_id=r.business_id AND v.status='active' AND v.attribute=a.key
 AND (a.key <> 'subcategory' OR v.parent_id=r.attributes->'category'->>'vocabulary_id'))) THEN
 RAISE EXCEPTION 'Vocabulary changed or hierarchy invalid; pilot batch not saved';
END IF;
INSERT INTO catalog_interpretations ({columns}, created_at)
 SELECT {selected}, now() FROM jsonb_to_recordset(payload) AS r({declaration})
 WHERE NOT EXISTS (SELECT 1 FROM catalog_interpretations i
 WHERE i.business_id=r.business_id AND i.product_id=r.product_id
 AND i.source_hash=r.source_hash AND ({fresh}))
 ON CONFLICT (business_id,product_id,source_hash,config_hash) DO NOTHING;
END {body_tag};
SELECT count(*) AS saved FROM catalog_interpretations WHERE run_id=
{data_tag}{rows[0]["run_id"]}{data_tag};
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=5, choices=range(1, 11))
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    if report["state"] != "completed":
        raise ValueError("Only a completed pilot may be exported")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for start in range(0, len(report["results"]), args.batch_size):
        path = args.output_dir / f"batch-{start // args.batch_size:03d}.sql"
        path.write_text(
            guarded_sql(report["results"][start : start + args.batch_size], report["business_id"]),
            encoding="utf-8",
        )
    print(json.dumps({"products": len(report["results"]), "output": str(args.output_dir)}))


if __name__ == "__main__":
    main()
