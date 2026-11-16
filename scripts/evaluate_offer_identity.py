"""Replay frozen readings/catalog through the existing pipeline; no PDF or DB access."""

import argparse
import hashlib
import json
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from time import monotonic

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.procurement.matching import ProductMatchService
from app.modules.procurement.page_analysis import PageOfferAnalysisService
from app.modules.procurement.page_analysis_schemas import PageAnalysisCheckpoint
from app.modules.procurement.schemas import Product


def evaluate_labels(result, cases):
    offers = {
        o["offer_id"]: o for o in result.document_metadata["analysis_ledger"]["interpretations"]
    }
    counts = Counter(
        correct_links=0,
        incorrect_links=0,
        pending=0,
        correct_abstentions=0,
        missed_links=0,
        no_match=0,
        correct_group_members=0,
        incorrect_group_members=0,
        missed_group_members=0,
    )
    rows = []
    for case in cases:
        offer = offers[case["offer"]["offer_id"]]
        expected, actual = case["expected_product_id"], offer["product_id"]
        if "expected_compatible_product_ids" in case:
            expected_members = set(case["expected_compatible_product_ids"])
            actual_members = set(offer.get("compatible_product_ids", []))
            counts["correct_group_members"] += len(expected_members & actual_members)
            counts["incorrect_group_members"] += len(actual_members - expected_members)
            counts["missed_group_members"] += len(expected_members - actual_members)
        if actual:
            counts["correct_links" if actual == expected else "incorrect_links"] += 1
        else:
            counts["pending" if offer["identity_status"] == "pending" else "no_match"] += 1
            counts["missed_links" if expected else "correct_abstentions"] += 1
        rows.append(
            {
                "offer_id": offer["offer_id"],
                "name": offer["raw_name"],
                "brand": offer["brand"],
                "category": case["category"],
                "expected_product_id": expected,
                "actual_product_id": actual,
                "scope": offer["identity_scope"],
                "status": offer["identity_status"],
                "reason_codes": offer["reason_codes"],
                "compatible_product_ids": offer.get("compatible_product_ids", []),
                "included_product_ids": offer.get("included_product_ids", []),
                "group_membership_status": offer.get("group_membership_status", "pending"),
            }
        )
    candidates = result.document_metadata["analysis_execution"]["identity_candidates"]
    positives = [c for c in cases if c["expected_product_id"]]
    recall = sum(any(
        p["product_id"] == c["expected_product_id"]
        for p in candidates.get(c["offer"]["offer_id"], [])
    ) for c in positives)
    return {
        "metrics": dict(counts), "cases": rows,
        "positive_retrieval_recall": {"hits": recall, "total": len(positives)},
    }


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument(
        "--labels",
        type=Path,
        default=Path("tests/fixtures/procurement/offer_identity_vital_20261022.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--execute", action="store_true", help="Pay only for labeled semantic cases"
    )
    parser.add_argument("--max-calls", type=int, default=1)
    parser.add_argument("--max-spend-usd", type=Decimal, default=Decimal("0.01"))
    parser.add_argument(
        "--provider", choices=["openai_decisions", "jev"], default="openai_decisions",
    )
    parser.add_argument(
        "--offer-id", action="append", default=None,
        help="Allow these labeled offer IDs, including abstention cases (repeatable)",
    )
    args = parser.parse_args()
    if (
        not 1 <= args.max_calls <= 6
        or not args.max_spend_usd.is_finite()
        or not 0 < args.max_spend_usd <= Decimal("0.02")
    ):
        parser.error("Evaluation limit: 1–6 requests, USD >0 and <=0.02")
    args.output.mkdir(parents=True, exist_ok=True)
    checkpoint_file = args.output / "identity-checkpoint.json"
    if args.output.resolve() == args.checkpoint.parent.resolve():
        parser.error("Use a separate output directory; preserve extraction evidence")
    original = PageAnalysisCheckpoint.model_validate_json(
        args.checkpoint.read_text(encoding="utf-8")
    )
    labels = json.loads(args.labels.read_text(encoding="utf-8"))
    labeled_ids = {c["offer"]["offer_id"] for c in labels["cases"]}
    if args.offer_id and not set(args.offer_id) <= labeled_ids:
        parser.error("All explicit offer IDs must be labeled before evaluation")
    allowed_ids = set(args.offer_id) if args.offer_id else {
        c["offer"]["offer_id"] for c in labels["cases"]
        if c["category"] == "semantic_description"
    }
    if original.business_id != labels["business_id"]:
        parser.error("Labels/checkpoint tenant mismatch")
    catalog = [
        Product.model_validate(p).model_copy(update={"auxiliary": None})
        for p in json.loads(args.catalog.read_text(encoding="utf-8"))
    ]

    class Snapshot:
        def list_products(self, *, business_id):
            return [p for p in catalog if p.business_id == business_id]

        def list_product_match_feedback_rules(self, **_):
            return []  # Frozen replay has no production feedback. Tests cover feedback separately.

    def retrieve(**kwargs):
        kwargs.pop("supplier_name")
        return ProductMatchService(Snapshot()).retrieve_analysis_candidates(
            supplier_id="evaluation",
            **kwargs,
        )

    service = PageOfferAnalysisService(reader=None, candidate_retriever=retrieve)
    started = monotonic()
    baseline = service._resolve(original.model_copy(deep=True), "Vital", None)
    baseline_seconds = monotonic() - started
    hashes = {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in {
            "checkpoint": args.checkpoint,
            "catalog": args.catalog,
            "labels": args.labels,
        }.items()
    }
    report = {
        "input_sha256": hashes,
        "business_id": original.business_id,
        "label_scope": (
            f"{len(labels['cases'])} observations; "
            f"{sum(bool(c['expected_product_id']) for c in labels['cases'])} positive identities; "
            "not a representative accuracy estimate"
        ),
        "baseline": evaluate_labels(baseline, labels["cases"]),
        "baseline_seconds": baseline_seconds,
        "baseline_summary": baseline.document_metadata["analysis"],
        "limits": {
            "max_calls": args.max_calls,
            "max_spend_usd": str(args.max_spend_usd),
            "max_input_tokens_per_call": 32000,
            "timeout_seconds": 20,
            "retries": 0,
            "images": 0,
        },
        "paid_execution": args.execute,
        "identity_provider": args.provider,
        "allowed_offer_ids": sorted(allowed_ids),
    }
    # Write the reviewable plan and frozen baseline BEFORE any paid request.
    write_json(args.output / "plan-and-baseline.json", report)
    write_json(args.output / "baseline.json", baseline.model_dump(mode="json"))
    if args.execute:
        from app.providers.offer_decisions import OpenAIOfferDecisions
        from app.settings import settings

        state = (
            PageAnalysisCheckpoint.model_validate_json(checkpoint_file.read_text(encoding="utf-8"))
            if checkpoint_file.exists()
            else original
        )
        if (
            state.business_id != original.business_id
            or state.run_id != original.run_id
            or state.source_sha256 != original.source_sha256
            or state.work != original.work
        ):
            parser.error("Evaluation checkpoint source/tenant mismatch")
        if args.provider == "jev":
            from app.providers.jev_offer_identity import JevOfferIdentityProvider

            service.decision_provider = JevOfferIdentityProvider(
                api_key=settings.typesafe_api_key, model=settings.jev_reconciliation_model,
                max_calls=args.max_calls, max_spend_usd=args.max_spend_usd,
                allowed_offer_ids=allowed_ids,
            )
        else:
            service.decision_provider = OpenAIOfferDecisions(
                api_key=settings.openai_api_key,
                max_calls=args.max_calls,
                max_spend_usd=args.max_spend_usd,
                allowed_offer_ids=allowed_ids,
            )
        prior_reservations = set(state.identity_reservations)
        started = monotonic()
        result = service._resolve(
            state,
            "Vital",
            None,
            save_checkpoint=lambda: write_json(checkpoint_file, state.model_dump(mode="json")),
        )
        report["combined_seconds"] = monotonic() - started
        report["combined"] = evaluate_labels(result, labels["cases"])
        report["combined_summary"] = result.document_metadata["analysis"]
        report["new_paid_requests"] = len(set(state.identity_reservations) - prior_reservations)
        report["reused_decisions"] = len(prior_reservations & set(state.identity_decisions))
        report["total_reserved_requests"] = len(state.identity_reservations)
        calls = [
            c
            for c in result.document_metadata["analysis_ledger"]["calls"]
            if c["provider"] in {"openai_decisions", "jev_identity"}
        ]
        report["decisions_calls"] = calls
        report["decisions_cost_usd_estimated"] = (
            str(
                sum(
                    (Decimal(c["cost"]["amount"]) for c in calls),
                    Decimal(0),
                )
            )
            if calls and all(c["cost"] for c in calls)
            else None
        )
        report["decisions_latency_seconds"] = sum(c["duration_seconds"] or 0 for c in calls)
        write_json(checkpoint_file, state.model_dump(mode="json"))
        write_json(args.output / "combined.json", result.model_dump(mode="json"))
    write_json(args.output / "report.json", report)
    print(
        json.dumps(
            {
                k: v
                for k, v in report.items()
                if k
                not in {
                    "baseline_summary",
                    "combined_summary",
                    "decisions_calls",
                }
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
