"""Materialize explicit pilot notices using current DB evidence, not edited report cells."""

from collections import Counter

from sqlalchemy import select

from app.db.models import CatalogInterpretationModel
from app.modules.procurement.catalog_family_suggestions import FamilySuggestionService
from app.modules.procurement.family_suggestion_schemas import (
    FamilyChangeRequest,
    FamilyCreationRequest,
)


def materialize_notice(session, business_id, row):
    if row.business_id != business_id:
        raise ValueError("Interpretation belongs to another business")
    notice = row.response.get("original_family_validation", {})
    service = FamilySuggestionService(session, business_id)
    common = {
        "product_id": row.product_id,
        "interpretation_id": row.id,
        "reason": notice.get("reason") or "Revisar familia original",
        "evidence": notice.get("evidence") or row.source["original_name"],
        "policy_version": "family-review-v2",
    }
    if notice.get("status") == "change_suggested":
        target = {"target_family_id": service.family_named(notice["suggested_family"]).id}
    elif notice.get("status") == "creation_suggested":
        definition = notice["new_family"]
        creation, _ = service.propose_creation(
            FamilyCreationRequest(
                **common,
                proposed_name=definition["name"],
                description=definition["description"],
                includes=definition["includes"],
                excludes=definition["excludes"],
            )
        )
        target = {"target_creation_suggestion_id": creation.id}
    else:
        return {}
    movement, outcome = service.propose_change(FamilyChangeRequest(**common, **target))
    return {
        "suggestion_id": movement.id,
        "suggestion_status": movement.status,
        "suggestion_outcome": outcome,
        **target,
    }


def materialize_report(session, business_id, report):
    if report.get("business_id") != business_id:
        raise ValueError("Report belongs to another business")
    counts = Counter()
    results = []
    service = FamilySuggestionService(session, business_id)
    seen = set()
    for exported in report["results"]:
        if exported["product_id"] in seen:
            raise ValueError("A report cannot contain multiple interpretations per product")
        seen.add(exported["product_id"])
        try:
            with session.begin_nested():
                row = session.scalar(
                    select(CatalogInterpretationModel).where(
                        CatalogInterpretationModel.business_id == business_id,
                        CatalogInterpretationModel.id == exported["id"],
                        CatalogInterpretationModel.product_id == exported["product_id"],
                    )
                )
                if row is None:
                    raise ValueError("Interpretation does not belong to business/product")
                notice = row.response.get("original_family_validation", {})
                if notice.get("status") != "change_suggested":
                    counts["not_a_change"] += 1
                    continue
                target = service.family_named(notice.get("suggested_family"))
                request = FamilyChangeRequest(
                    product_id=row.product_id,
                    interpretation_id=row.id,
                    target_family_id=target.id,
                    reason=notice.get("reason") or "Revisar familia original",
                    evidence=notice.get("evidence") or row.source["original_name"],
                    policy_version="family-review-v1/legacy-notice",
                )
                suggestion, outcome = service.propose_change(request)
                counts[outcome] += 1
                results.append(
                    {
                        "id": suggestion.id,
                        "product_id": row.product_id,
                        "status": suggestion.status,
                        "outcome": outcome,
                    }
                )
        except ValueError as exc:
            counts["conflicts_or_stale"] += 1
            results.append({"product_id": exported["product_id"], "error": str(exc)})
    session.flush()
    return {"business_id": business_id, "counts": dict(counts), "results": results}
