"""Offline inspection only. Frozen JSON and pure contracts; no DB or network."""
import copy
import json
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app.modules.procurement.analysis_contracts import OfferTextBlock
from app.modules.procurement.block_interpretation import detect_value_candidates, price_occurrences
from app.modules.procurement.document_analysis import AnalysisBudget
from app.providers.jev_analysis import JevAnalysisClient, build_classification_questions, _block_state

def size(payload):
    return len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"))

def read(name):
    return json.loads((ROOT / "output/diagnostics" / name).read_text(encoding="utf-8-sig"))

ledger = read("jev-run-4a3058e1-full.json")["metadata"]["analysis_ledger"]
audits = {a["block_id"]: a for a in ledger["decision_audit"]}
prompt = (ROOT / "app/assistant/prompts/jev_fragment_classification.md").read_text(encoding="utf-8")
rows = []
for raw in ledger["blocks"]:
    raw = copy.deepcopy(raw)
    raw["reason_codes"] = []
    raw["derived_offer_ids"] = []
    block = OfferTextBlock.model_validate(raw)
    candidates = detect_value_candidates(block=block)
    questions = build_classification_questions(block=block, candidates=candidates)
    fragments = block.fragments + [f for f in block.page_conditions if f.fragment_id in block.condition_refs]
    descriptions = {x.fragment_id: x.model_dump(mode="json") for x in fragments}
    descriptions.update({c.candidate_id: c.model_dump(mode="json") for c in candidates})
    state = {"block": _block_state(block), "occurrences": [c.model_dump(mode="json") for c in candidates]}
    original = {"model": "jev-1.13.0", "state": state, "questions": {
        q.question_id: {"type": "choice", "instructions": f"{prompt}\n\n{q.prompt}",
                        "criteria": {o: descriptions.get(o) for o in q.options}}
        for q in questions}}
    compact = copy.deepcopy(original)
    compact["state"]["policy_rules"] = prompt
    compact["questions"] = {q.question_id: {
        "type": "choice", "instructions": "Apply state.policy_rules. " + q.prompt,
        "criteria": {o: None for o in q.options}} for q in questions}
    stage_one = copy.deepcopy(compact)
    stage_one["questions"] = {k:v for k,v in compact["questions"].items() if k.endswith(":role")}
    money = [c for c in price_occurrences(block, candidates) if c.kind == "money"]
    row = {"block_id": block.block_id, "page": block.page_number, "order": block.order_on_page,
           "questions": len(questions), "price_occurrences": len(price_occurrences(block, candidates)),
           "money_occurrences": len(money), "original_bytes": size(original),
           "compact_same_questions_bytes": size(compact), "compact_role_only_bytes": size(stage_one),
           "recorded_local_limit": "jev_request_limit" in audits.get(block.block_id, {}).get("reason_codes", []),
           "has_audit": block.block_id in audits,
           "questions_match_saved": [q.model_dump(mode="json") for q in questions] == audits.get(block.block_id, {}).get("questions") if block.block_id in audits else None}
    rows.append(row)
    if len(rows) == 1:
        transport_calls = []
        def forbidden_transport(*args):
            transport_calls.append(1)
            raise AssertionError("Unexpected external transport")
        budget = AnalysisBudget(max_calls=100)
        assert budget.reserve()
        client = JevAnalysisClient(api_key="offline-synthetic-key", transport=forbidden_transport)
        result = client.evaluate(state=original["state"], questions=original["questions"])
        assert result.error == "request_limit" and not result.attempted and not transport_calls
        assert budget.reserved == 1 and client.call_count == 0
        first = {"text": block.full_text, "text_candidates": [c.literal for c in candidates if c.kind == "text"],
                 "price_occurrences": [c.literal for c in price_occurrences(block, candidates)],
                 "fake_transport_calls": len(transport_calls), "reserved_after_local_rejection": budget.reserved}

saved = read("jev-run-4a3058e1-payload-sizes.json")["blocks"]
recorded_sizes = {r["block_id"]: r["reconstructed_request_bytes"] for r in saved}
def summarize(values):
    values = sorted(values)
    return {"min": values[0], "median": values[len(values)//2], "p95": values[int(.95*(len(values)-1))], "max": values[-1], "sum":sum(values)}
result = {
    "note": "Reconstructed wire bytes. Compact layouts are unimplemented experiments, not provider quality/token/latency evidence.",
    "actual_calls": dict(Counter((c["provider"]+":"+c["phase"]+":"+c["status"]) for c in ledger["calls"])),
    "audits": len(audits), "blocks":len(rows), "audited_questions_equal": all(r["questions_match_saved"] for r in rows if r["has_audit"]),
    "over_limit_audited":sum(r["original_bytes"]>100000 for r in rows if r["has_audit"]),
    "size_equal_previous_reconstruction":all(r["original_bytes"]==recorded_sizes[r["block_id"]] for r in rows if r["block_id"] in recorded_sizes),
    "limit_match_every_audit": all((r["original_bytes"]>100000)==r["recorded_local_limit"] for r in rows if r["has_audit"]),
    "read_pages": [p["page_number"] for p in ledger["pages"] if p["reading_ids"]],
    "unread_pages": [p["page_number"] for p in ledger["pages"] if not p["reading_ids"]],
    "question_sum_all_blocks": sum(r["questions"] for r in rows),
    "price_occurrence_sum_all_blocks":sum(r["price_occurrences"] for r in rows),
    "money_occurrence_sum_all_blocks":sum(r["money_occurrences"] for r in rows),
    "sizes_all_125":{k:summarize([r[k] for r in rows]) for k in ["original_bytes","compact_same_questions_bytes","compact_role_only_bytes"]},
    "first_block":first, "rows":rows}
target = Path(__file__).with_name("audit_case_b_results.json")
target.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n",encoding="utf-8")
print(json.dumps({k:v for k,v in result.items() if k!="rows"},ensure_ascii=False,indent=2))
