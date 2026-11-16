"""Local paired comparison: replay the exact Decisions text/choices through JEV."""

import argparse
import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.procurement.analysis_contracts import (
    IdentityCandidate,
    OfferInterpretation,
    OfferTextBlock,
)
from app.modules.procurement.decision_policy import (
    DocumentDecisionPolicy,
    build_offer_decision_request,
)
from app.providers.jev_analysis import JevAnalysisClient
from app.providers.offer_decisions import OpenAIOfferDecisions
from scripts.evaluate_offer_identity import write_json

CASE_IDS = (
    "82e46783-656b-521b-bea6-45c38b31a4c4",  # Quilmes
    "48f760f1-2b65-5fba-b4f3-901a218cf61c",  # CIF
    "5eaa4cae-4f47-562f-95af-6706acc901ff",  # Sedal
)
RATE = Decimal("0.042")  # Official TypeSafe standard input USD/million; output free.
RESERVE_RATE = Decimal("0.10")
MAX_BYTES = 16000
MAX_USD = Decimal("0.01")


def paired_case(source, offer_id):
    metadata = source["document_metadata"]
    ledger = metadata["analysis_ledger"]
    raw_offer = next(o for o in ledger["interpretations"] if o["offer_id"] == offer_id)
    offer = OfferInterpretation.model_validate(raw_offer)
    block = OfferTextBlock.model_validate(next(
        b for b in ledger["blocks"] if b["block_id"] == offer.block_id
    ))
    candidates = [IdentityCandidate.model_validate(c) for c in
                  metadata["analysis_execution"]["identity_candidates"][offer_id]]
    request = build_offer_decision_request(
        business_id=offer.business_id, offer=offer, block=block, candidates=candidates,
        policy=DocumentDecisionPolicy(require_identity_evidence=True),
    )
    request.questions = [q for q in request.questions if q.question_id.endswith(":identity")]
    encoder = OpenAIOfferDecisions(api_key="offline-payload-only")
    payload = encoder.payload(request)
    audit = next(a for a in ledger["decision_audit"] if a["offer_id"] == offer_id)
    old = audit["decisions"][0]
    if old["prompt_version"] != "offer_identity_decisions_v2:" + encoder.config_id:
        raise ValueError("Source prompt changed; cannot claim an identical-input comparison")
    q = payload["questions"][0]
    return {
        "offer_id": offer_id, "brand": offer.brand,
        "state": payload["input"],  # Identical text, including the exact shared prompt and JSON.
        "questions": {q["name"]: {
            "type": "choice", "instructions": q["instructions"],
            "criteria": {c["value"]: c["value"] for c in q["choices"]},
        }},
        "decisions_answer": old,
    }


def multiple_case(pair):
    """Independent questions permit 1,2,3,4 without declaring the entire set included."""
    data = json.loads(pair["state"].split("\n")[-1])
    prompt = (Path(__file__).parents[1] / (
        "app/assistant/prompts/offer_group_membership_v1.md"
    )).read_text(encoding="utf-8")
    questions = {}
    for number, candidate in enumerate(data["candidates"], 1):
        candidate["candidate_number"] = number
        target = json.dumps(candidate, ensure_ascii=False)
        questions[f"candidate_{number}_compatible"] = {
            "type": "choice",
            "instructions": "Aplicá las definiciones de state.instructions.\nCANDIDATO:\n" + target
            + "\n¿Es compatible con la descripción general de la oferta?",
            "criteria": {
                "compatible": "Marca, tipo y contenido compatibles sin contradicción observada."
                " La variedad no especificada no cuenta como contradicción.",
                "incompatible": "Existe una contradicción observada de marca, tipo o presentación.",
                "insufficient": "No alcanza la evidencia para evaluar compatibilidad general.",
            },
        }
        questions[f"candidate_{number}_included"] = {
            "type": "choice",
            "instructions": "Aplicá las definiciones de state.instructions.\nCANDIDATO:\n" + target
            + "\n¿La evidencia permite afirmar que este producto está incluido en la oferta?",
            "criteria": {
                "included": "La evidencia permite afirmar su inclusión concreta en la oferta.",
                "excluded": "La evidencia permite afirmar que no está incluido.",
                "insufficient": "Puede ser compatible, pero no alcanza para afirmar inclusión"
                " ni exclusión del producto concreto.",
            },
        }
    return {
        "offer_id": pair["offer_id"], "brand": pair["brand"],
        "state": {"instructions": prompt, **data}, "questions": questions,
        "single_selection_answer": pair["decisions_answer"],
    }


def multiple_selection(case, answers):
    compatible, included = [], []
    for number, _ in enumerate(case["state"]["candidates"], 1):
        for purpose, affirmative, target in (
            ("compatible", "compatible", compatible), ("included", "included", included),
        ):
            answer = answers.get(f"candidate_{number}_{purpose}")
            if (answer and answer.choice == affirmative and answer.confidence >= Decimal("0.85")
                    and answer.probabilities[affirmative] >= Decimal("0.85")):
                target.append(number)
    return {
        "compatible_candidate_numbers": compatible,
        "included_candidate_numbers": included,
        "note": "Compatible candidates are possible members, not confirmed promotion coverage.",
    }


def run(args):
    source = json.loads(args.source.read_text(encoding="utf-8"))
    multiple = args.selection_mode == "multiple"
    cases = [paired_case(source, offer_id) for offer_id in (CASE_IDS[1:] if multiple else CASE_IDS)]
    if multiple:
        cases = [multiple_case(case) for case in cases]
    plan = {
        "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
        "model": "jev-1.13.0", "cases": cases,
        "limits": {"requests": len(cases), "request_bytes": MAX_BYTES, "timeout_seconds": 20,
                   "max_usd": str(MAX_USD), "reserve_usd_per_million": str(RESERVE_RATE),
                   "retries": 0, "images": 0},
        "pricing_source": "https://typesafe.ai/",
    }
    args.output.mkdir(parents=True, exist_ok=True)
    write_json(args.output / "plan.json", plan)
    if not args.execute:
        print("Plan written; no network requests")
        return
    from app.settings import settings

    if not settings.typesafe_api_key:
        raise ValueError("JEV key not configured")
    client = JevAnalysisClient(
        api_key=settings.typesafe_api_key, model=plan["model"], max_calls=len(cases),
        max_questions=24 if multiple else 1, max_concurrency=1, max_request_bytes=MAX_BYTES,
        max_response_bytes=128000, timeout_seconds=20,
    )
    checkpoint = args.output / "results.json"
    fingerprint = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    report = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {
        "plan_sha256": fingerprint, "reserved_usd": "0", "cases": [],
    }
    if report["plan_sha256"] != fingerprint:
        raise ValueError("Comparison checkpoint/configuration mismatch")
    if any(c["status"] != "success" or c.get("input_tokens") is None for c in report["cases"]):
        raise ValueError("Previous attempt uncertain or failed; automatic retries disabled")
    for case in cases:
        if any(c["offer_id"] == case["offer_id"] for c in report["cases"]):
            continue
        kwargs = {k: case[k] for k in ("state", "questions")}
        preflight = client.preflight(**kwargs)
        if preflight:
            raise ValueError("Local preflight failed")
        token_bound = client.request_bytes(**kwargs) + 4096
        reservation = Decimal(token_bound) * RESERVE_RATE / 1_000_000
        reserved = Decimal(report["reserved_usd"]) + reservation
        if len(report["cases"]) >= len(cases) or reserved > MAX_USD:
            break
        row = {"offer_id": case["offer_id"], "brand": case["brand"], "status": "inflight",
               "reserved_usd": str(reservation),
               "single_selection_answer": case.get(
                   "single_selection_answer", case.get("decisions_answer"),
               )}
        report["cases"].append(row)
        report["reserved_usd"] = str(reserved)
        write_json(checkpoint, report)  # Durable marker BEFORE sending a paid request.
        result = client.evaluate(**kwargs)
        row.update({
            "status": "success" if result.answers and not result.error else "error",
            "error": result.error, "answer_errors": result.errors,
            "duration_seconds": result.duration_seconds,
            "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
            "jev_answers": {k: {
                "choice": v.choice, "confidence": str(v.confidence),
                "probabilities": {key: str(p) for key, p in v.probabilities.items()},
            } for k, v in result.answers.items()},
            "estimated_cost_usd": str(Decimal(result.input_tokens) * RATE / 1_000_000)
            if result.input_tokens is not None else None,
        })
        if multiple:
            row["multiple_selection"] = multiple_selection(case, result.answers)
        write_json(checkpoint, report)
        if row["status"] != "success" or result.input_tokens is None:
            break
        if result.input_tokens > token_bound:
            raise ValueError("Token reservation exceeded; halted")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--selection-mode", choices=["single", "multiple"], default="single")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
