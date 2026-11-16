"""Reconcile report with DB and quarantine unsupported subtype inference."""
# ruff: noqa: E402

import argparse
import json
import sys
from collections import Counter
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from app.db.models import CatalogInterpretationModel, CatalogVocabularyModel
from app.db.repositories.catalog_taxonomy_changes import apply_taxonomy_changes
from app.db.session import SessionLocal
from app.modules.procurement.catalog_full_interpretation import guard_inferred_subtype
from app.modules.procurement.catalog_interpretation import interpretation_columns
from app.modules.procurement.normalization import normalize_text
from app.providers.catalog_taxonomy_proposal import FullCatalogProposal
from scripts.interpret_catalog import export_row


def finalize(path, baseline=None):
    report = json.loads(path.read_text(encoding="utf-8"))
    baseline_rows = {}
    if baseline:
        saved = json.loads(baseline.read_text(encoding="utf-8"))
        if saved["business_id"] != report["business_id"]:
            raise ValueError("Baseline belongs to another business")
        baseline_rows = {r["id"]: r for r in saved["results"]}
    corrections = []
    with SessionLocal() as session:
        for exported in report["results"]:
            row = session.scalar(
                select(CatalogInterpretationModel).where(
                    CatalogInterpretationModel.id == exported["id"],
                    CatalogInterpretationModel.business_id == report["business_id"],
                )
            )
            if row is None or row.source != exported["source"]:
                raise ValueError("Interpretation outside business or stale report")
            raw = row.response["full_proposal"]
            parsed = raw.get("backend_normalization") or next(
                c["parsed"] for o in raw["output"] for c in o.get("content", []) if c.get("parsed")
            )
            proposal = FullCatalogProposal.model_validate(parsed)
            attributes = deepcopy(row.attributes)
            saved = baseline_rows.get(row.id)
            if (
                saved
                and saved["source"] == row.source
                and not row.category
                and saved["category"]
                and attributes["category"]["status"] == "unsupported"
                and saved["attributes"]["category"].get("probability", 0) >= 0.9
            ):
                attributes["category"] = deepcopy(saved["attributes"]["category"])
                attributes["product_type"] = deepcopy(attributes["category"])
                row.response = {**row.response, "parent_link_repaired_from_baseline": str(baseline)}
            guard_inferred_subtype(attributes, proposal)
            if attributes != row.attributes:
                row.attributes = attributes
                for field, value in interpretation_columns(
                    attributes, row.source["original_name"]
                ).items():
                    setattr(row, field, value)
                corrections.append(row.product_id)
                key = row.product_id
                report["review_flags"][key] = (
                    report["review_flags"].get(key, "")
                    + " Subcategoría inferida solo por marca: comprobar antes de confirmar."
                ).strip()
                node = session.scalar(
                    select(CatalogVocabularyModel).where(
                        CatalogVocabularyModel.id == attributes["subcategory"].get("vocabulary_id"),
                        CatalogVocabularyModel.business_id == report["business_id"],
                    )
                )
                if node and proposal.brand.evidence:
                    aliases = [
                        a
                        for a in node.aliases
                        if normalize_text(a) != normalize_text(proposal.brand.evidence)
                    ]
                    if aliases != node.aliases:
                        change = apply_taxonomy_changes(
                            session,
                            report["business_id"],
                            [
                                {
                                    "action": "replace_aliases",
                                    "target_id": node.id,
                                    "aliases": aliases,
                                    "reason": "Una marca no demuestra equivalencia con el subtipo",
                                    "evidence": proposal.brand.evidence,
                                }
                            ],
                            {"product_names": [row.source["original_name"]]},
                        )
                        report["audit_changes"].append(
                            {
                                "action": "replace_aliases",
                                "label": node.label,
                                "before_type": "subcategory",
                                "after_type": "subcategory",
                                "before_parent": node.parent_id,
                                "after_parent": node.parent_id,
                                "reason": "Eliminar equivalencia incorrecta entre marca y subtipo",
                                "change_id": change.id,
                            }
                        )
        session.commit()
        for i, exported in enumerate(report["results"]):
            row = session.scalar(
                select(CatalogInterpretationModel).where(
                    CatalogInterpretationModel.id == exported["id"],
                    CatalogInterpretationModel.business_id == report["business_id"],
                )
            )
            # Regenerate display names after the deterministic normalization change.
            row.standard_name = interpretation_columns(row.attributes, row.source["original_name"])[
                "standard_name"
            ]
            report["results"][i] = export_row(row, cached=exported["cached"])
        session.commit()
    report["summary"] = {
        **report.get("summary", {}),
        "with_category": sum(bool(r["category"]) for r in report["results"]),
        "with_subcategory": sum(bool(r["subcategory"]) for r in report["results"]),
        "resolved_brand": sum(bool(r["brand"]) for r in report["results"]),
        "resolved_variant": sum(bool(r["variant"]) for r in report["results"]),
        "subtypes_quarantined": corrections,
        "family_checks": dict(
            Counter(
                r["response"]["original_family_validation"]["status"] for r in report["results"]
            )
        ),
    }
    assert all(
        not r["category"] or r["attributes"]["category"].get("probability", 1) >= 0.9
        for r in report["results"]
    )
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--baseline", type=Path)
    args = parser.parse_args()
    finalize(args.report, args.baseline)
