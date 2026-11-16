"""Recheck an explicit disputed subset, preserving the rest of the paid sample."""
# ruff: noqa: E402

import argparse
import json
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.models import CatalogInterpretationModel
from app.modules.procurement.catalog_full_interpretation import validate_structure
from app.modules.procurement.catalog_interpretation import interpretation_columns
from app.providers.catalog_taxonomy_proposal import FullCatalogProposal
from app.settings import settings
from scripts.interpret_catalog import export_row
from scripts.interpret_catalog_full import run_full


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--feedback", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    feedback = json.loads(args.feedback.read_text(encoding="utf-8"))
    if not feedback or not set(feedback) <= {r["product_id"] for r in report["results"]}:
        raise ValueError("Feedback must select products in the sample")
    settings.database_url = "sqlite:///" + args.database.resolve().as_posix()
    reviewed = run_full(
        argparse.Namespace(
            business_id=report["business_id"],
            product_id=list(feedback),
            limit=len(feedback),
            model="gpt-6-luna",
            threshold=0.9,
            output=args.output,
            review_feedback=feedback,
        )
    )
    if reviewed["errors"] or len(reviewed["results"]) != len(feedback):
        raise ValueError("The ambiguity pass is incomplete; original sample preserved")
    replacements = {r["product_id"]: r for r in reviewed["results"]}
    report["results"] = [replacements.get(r["product_id"], r) for r in report["results"]]
    for key in (
        "api_calls",
        "decisions_calls",
        "generative_calls",
        "input_tokens",
        "output_tokens",
        "estimated_cost_usd",
    ):
        report[key] += reviewed[key]
    report["estimated_cost_incomplete"] |= reviewed["estimated_cost_incomplete"]
    report["audit_changes"].extend(reviewed["audit_changes"])
    with Session(create_engine(settings.database_url)) as session:
        for i, exported in enumerate(report["results"]):
            row = session.scalar(
                select(CatalogInterpretationModel).where(
                    CatalogInterpretationModel.business_id == report["business_id"],
                    CatalogInterpretationModel.id == exported["id"],
                )
            )
            raw = row.response["full_proposal"]
            parsed = raw.get("backend_normalization") or next(
                c["parsed"] for o in raw["output"] for c in o.get("content", []) if c.get("parsed")
            )
            proposal = FullCatalogProposal.model_validate(parsed)
            attributes = deepcopy(row.attributes)
            validate_structure(attributes, proposal)
            if attributes != row.attributes:
                row.response = {
                    **row.response,
                    "backend_policy_adjustments": {
                        "reason": "Remove repeated category/subcategory labels",
                        "previous_attributes": row.attributes,
                    },
                }
                row.attributes = attributes
                for key, value in interpretation_columns(
                    attributes, row.source["original_name"]
                ).items():
                    setattr(row, key, value)
            report["results"][i] = export_row(row, cached=exported["cached"])
        session.commit()
    for key, comparison in report["comparison"].items():
        comparison["after"] = sum(bool(r.get(key)) for r in report["results"])
    report["summary"] = {
        "with_category": report["comparison"]["category"]["after"],
        "with_subcategory": report["comparison"]["subcategory"]["after"],
        "resolved_brand": report["comparison"]["brand"]["after"],
        "resolved_variant": report["comparison"]["variant"]["after"],
        "family_checks": dict(
            Counter(
                r["response"]["original_family_validation"]["status"] for r in report["results"]
            )
        ),
    }
    report["ambiguity_review"] = {
        "run_id": reviewed["run_id"],
        "feedback": feedback,
        "interpretation_ids": [r["id"] for r in reviewed["results"]],
    }
    report["review_flags"] = {
        r["product_id"]: r["response"]["original_family_validation"].get("reason", "")
        for r in report["results"]
        if r["response"]["original_family_validation"].get("requires_user_review")
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"reviewed": len(feedback), "summary": report["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
