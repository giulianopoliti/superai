"""Resumable 100-product pilot from a Cloud snapshot; dry-run by default."""
# ruff: noqa: E402

import argparse
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from time import monotonic
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.procurement.catalog_decisions_first import (
    VERSION,
    accept_answers,
    family_result,
    finish_attributes,
    options_audit,
    prepare_options,
    questions_for,
    taxonomy_options,
)
from app.modules.procurement.catalog_interpretation import digest
from app.modules.procurement.catalog_taxonomy import validate_proposal
from app.providers.catalog_auxiliary_gap import PROMPT as GAP_PROMPT
from app.providers.catalog_auxiliary_gap import OpenAIAuxiliaryGap
from app.providers.catalog_decisions import (
    CatalogDecisionResponse,
    CatalogDecisionsHTTPError,
    OpenAICatalogDecisions,
)
from app.providers.catalog_taxonomy_proposal import TaxonomyProposal
from app.settings import settings

ROOT = Path(__file__).resolve().parents[1]
PROMPT = (ROOT / "app/assistant/prompts/catalog_decisions_first.md").read_text(encoding="utf-8")


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


class BudgetStop(RuntimeError):
    pass


class CallJournal:
    """Single-worker ledger. An ambiguous in-flight call blocks automatic repeats."""

    def __init__(self, path, budget):
        self.path = path
        self.budget = Decimal(str(budget))
        self.records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    @property
    def cost(self):
        return sum((Decimal(str(r.get("cost_usd", 0))) for r in self.records.values()), Decimal(0))

    def call(self, stage, payload, invoke, parse):
        key = digest({"stage": stage, "payload": payload})
        if key in self.records:
            record = self.records[key]
            if record["state"] != "received":
                raise BudgetStop("An earlier call has unknown consumption; inspect the ledger")
            return parse(record["response"])
        if any(
            r["state"] not in {"received", "rejected", "interrupted_reserved"}
            for r in self.records.values()
        ):
            raise BudgetStop("Unknown consumption blocks new calls")
        # UTF-8 bytes are a deliberately conservative upper bound on input tokens.
        # Include schema/prompt overhead and the complete 800-token generative output.
        token_bound = len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + 4096
        reserve = Decimal(token_bound) * Decimal("0.125" if stage == "gap" else "0.10")
        if stage == "gap":
            reserve += Decimal(800) * Decimal("0.50")
        reserve /= 1_000_000
        if self.cost + reserve > self.budget:
            raise BudgetStop("Budget insufficient for the next bounded request")
        if token_bound > 60000:
            raise BudgetStop("Context exceeds pilot bound; expand hierarchically before calling")
        record = {
            "state": "in_flight",
            "stage": stage,
            "reserved_usd": float(reserve),
            "started_at": datetime.now(UTC).isoformat(),
        }
        self.records[key] = record
        save_json(self.path, self.records)

        def received(raw):
            # Save raw response first, including when later schema validation fails.
            record.update(response=raw, state="usage_unknown")
            usage = raw.get("usage") or {}
            if "input_tokens" in usage:
                input_tokens = usage["input_tokens"]
                if stage == "gap":
                    details = usage.get("input_tokens_details") or {}
                    cached = details.get("cached_tokens", 0)
                    written = details.get("cache_write_tokens", 0)
                    cost = (
                        max(0, input_tokens - cached - written) * 0.10
                        + cached * 0.01
                        + written * 0.125
                        + usage.get("output_tokens", 0) * 0.50
                    ) / 1e6
                else:
                    cost = input_tokens * 0.10 / 1e6
                record.update(cost_usd=cost, state="received")
            save_json(self.path, self.records)
            if record["state"] != "received":
                raise BudgetStop("Provider response has no usage; inspect before continuing")

        try:
            invoke(received)
        except CatalogDecisionsHTTPError as exc:
            if exc.status_code in {400, 401, 403, 404, 422, 429}:
                record.update(
                    state="rejected", cost_usd=0, http_status=exc.status_code, error=exc.details
                )
                save_json(self.path, self.records)
            raise
        if record["state"] != "received":
            raise BudgetStop("Provider did not journal its response")
        return parse(record["response"])


def parse_gap(raw):
    text = "".join(
        c.get("text", "")
        for item in raw.get("output", [])
        for c in item.get("content", [])
        if c.get("type") == "output_text"
    )
    return TaxonomyProposal.model_validate_json(text)


class Pilot:
    def __init__(self, chooser, proposer, journal, *, threshold=0.9, max_proposals=10):
        self.chooser = chooser
        self.proposer = proposer
        self.journal = journal
        self.threshold = threshold
        self.max_proposals = max_proposals

    def decide(self, source, options, stage):
        questions = questions_for(options)
        if any(len(q["choices"]) > 255 for q in questions):
            raise ValueError("Choice group exceeds API limit; split it before sending")
        # Wire-local IDs preserve every option and avoid paying for hundreds of UUIDs.
        mappings = {}
        for question in questions:
            mapping = {}
            for index, choice in enumerate(question["choices"]):
                wire_id = f"o{index}"
                mapping[wire_id] = choice["value"]
                choice["value"] = wire_id
            mappings[question["name"]] = mapping
        # Full POS snapshot is kept in audit, not repeated as prompt boilerplate.
        data = {k: source[k] for k in ("original_name", "brand", "unit_size", "unit")}
        data["current_family"] = source.get("category")
        if "selected_category" in source:
            data["selected_category"] = source["selected_category"]
        payload = {
            "model": "gpt-6-luna",
            "prompt": PROMPT,
            "input": data,
            "questions": questions,
            "business_id": source["business_id"],
            "option_mapping": mappings,
        }

        def invoke(callback):
            self.chooser.on_response = callback
            self.chooser.decide(data, questions)

        def parse(raw):
            response = CatalogDecisionResponse.model_validate(raw)
            for answer in response.answers:
                if answer.type == "refusal":
                    continue
                mapping = mappings[answer.name]
                answer.choice = mapping[answer.choice]
                for probability in answer.probabilities:
                    probability.value = mapping[probability.value]
            return response

        response = self.journal.call(stage, payload, invoke, parse)
        return accept_answers(response, options, source, self.threshold)

    def gap(self, source, attribute, entries, parent_id):
        scoped = [
            {k: e[k] for k in ("id", "attribute", "label", "aliases", "parent_id", "status")}
            for e in entries
            if e["attribute"] == attribute and e["parent_id"] == parent_id
        ]
        payload = {
            "prompt": GAP_PROMPT,
            "model": "gpt-6-luna",
            "effort": self.proposer.effort,
            "source": source,
            "attribute": attribute,
            "entries": scoped,
            "parent_id": parent_id,
            "schema": TaxonomyProposal.model_json_schema(),
        }
        key = digest({"stage": "gap", "payload": payload})
        if (
            key not in self.journal.records
            and sum(r["stage"] == "gap" for r in self.journal.records.values())
            >= self.max_proposals
        ):
            return {"status": "needs_review", "value": None, "reason": "proposal_limit"}

        def invoke(callback):
            self.proposer.on_response = callback
            self.proposer.propose(source, attribute, scoped, parent_id)

        proposal = self.journal.call("gap", payload, invoke, parse_gap)
        state = validate_proposal(
            proposal, source=source, attribute=attribute, entries=entries, parent_id=parent_id
        )
        # New nodes remain proposals. They do not become accepted merely on creation.
        state.setdefault("value", None)
        state["proposal"] = proposal.model_dump()
        return state

    def interpret(self, source, entries, families):
        options, audit = prepare_options(source, entries, families)
        states = self.decide(source, options, "classification")
        family = family_result(source, states.pop("original_family"))
        groups = {k: states.pop(k) for k in list(states) if k.startswith("category_group_")}
        if groups:
            finalists = [
                o
                for k, values in options.items()
                if k in groups
                for o in values
                if groups[k].get("choice") == o.value
            ]
            if finalists:
                states.update(self.decide(source, {"category": finalists}, "category_finalists"))
            elif all(
                s.get("status") == "none" and s.get("probability", 0) >= self.threshold
                for s in groups.values()
            ):
                states["category"] = {
                    "status": "none",
                    "value": None,
                    "probability": min(s["probability"] for s in groups.values()),
                }
            else:
                states["category"] = {"status": "unknown", "value": None}
            audit["category_group_answers"] = groups
        category = states["category"]
        if category.get("status") == "none" and category.get("probability", 0) >= self.threshold:
            states["category"] = category = self.gap(source, "category", entries, None)
        if category.get("value") and category.get("vocabulary_id"):
            sub_options = {
                "subcategory": taxonomy_options(
                    source,
                    entries,
                    "subcategory",
                    category["vocabulary_id"],
                )
            }
            # The second decision is needed to keep hierarchy coherent.
            sub_source = {**source, "selected_category": category["value"]}
            states.update(self.decide(sub_source, sub_options, "subcategory"))
            sub = states["subcategory"]
            if sub.get("status") == "none" and sub.get("probability", 0) >= self.threshold:
                states["subcategory"] = self.gap(
                    source,
                    "subcategory",
                    entries,
                    category["vocabulary_id"],
                )
            audit["subcategory_options"] = options_audit(sub_options)
        columns = finish_attributes(states)
        if not columns["standard_name"]:
            columns["standard_name"] = source["original_name"]
        config_hash = digest(
            {
                "version": VERSION,
                "prompt": PROMPT,
                "gap_prompt": GAP_PROMPT,
                "threshold": self.threshold,
                "effort": self.proposer.effort,
            }
        )
        return {
            "id": str(uuid4()),
            "business_id": source["business_id"],
            "product_id": source["product_id"],
            "source": source,
            "source_hash": digest(source),
            "config_hash": config_hash,
            "attributes": states,
            "request": {
                "prompt": PROMPT,
                "policy": VERSION,
                "candidates": options_audit(options),
                "candidate_audit": audit,
            },
            "response": {"original_family_validation": family, "policy": VERSION},
            **columns,
        }


def run(
    snapshot,
    output,
    *,
    execute=False,
    limit=100,
    budget=0.10,
    effort="none",
    max_proposals=10,
    chooser=None,
    proposer=None,
):
    if not 1 <= limit <= 100 or not 0 < budget <= 1 or not 0 <= max_proposals <= 100:
        raise ValueError("Pilot bounds: 1..100 products, budget <= USD 1, 0..100 proposals")
    business_id = snapshot["business_id"]
    sources = snapshot["sources"][:limit]
    if len({s["product_id"] for s in sources}) != len(sources) or any(
        s["business_id"] != business_id for s in sources
    ):
        raise ValueError("Invalid snapshot tenant or duplicate product")
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / "report.json"
    signature = digest(
        {
            "snapshot": snapshot,
            "limit": limit,
            "effort": effort,
            "max_proposals": max_proposals,
            "prompt": PROMPT,
            "gap_prompt": GAP_PROMPT,
            "version": VERSION,
        }
    )
    if manifest.exists():
        report = json.loads(manifest.read_text(encoding="utf-8"))
        if report["signature"] != signature:
            raise ValueError("Output belongs to another selection/configuration")
    else:
        report = {
            "run_id": str(uuid4()),
            "signature": signature,
            "business_id": business_id,
            "version": VERSION,
            "started_at": datetime.now(UTC).isoformat(),
            "selected": len(sources),
            "results": [],
            "errors": [],
            "source_fingerprint": snapshot.get("source_fingerprint"),
        }
    report.update(state="running" if execute else "dry_run", budget_usd=budget)
    if not execute:
        audits = [
            prepare_options(s, snapshot["vocabulary"], snapshot["families"])[1] for s in sources
        ]
        report["candidate_preview"] = {
            "products_with_omissions": sum(bool(a["omitted_spans"]) for a in audits),
            "paid_calls": 0,
        }
        save_json(manifest, report)
        return report
    journal = CallJournal(output / "calls.json", budget)
    pilot = Pilot(
        chooser
        or OpenAICatalogDecisions(
            settings.openai_api_key,
            prompt_path=ROOT / "app/assistant/prompts/catalog_decisions_first.md",
            max_attempts=1,
        ),
        proposer or OpenAIAuxiliaryGap(settings.openai_api_key, effort=effort),
        journal,
        max_proposals=max_proposals,
    )
    completed = {r["product_id"] for r in report["results"]}
    started = monotonic()
    save_json(manifest, report)
    for source in sources:
        if (output / "STOP").exists():
            report["state"] = "operator_stopped"
            break
        if source["product_id"] in completed:
            continue
        try:
            row = pilot.interpret(source, snapshot["vocabulary"], snapshot["families"])
            row["run_id"] = report["run_id"]
            # Decimal columns are serialized as strings for exact persistence later.
            for k, value in row.items():
                if isinstance(value, Decimal):
                    row[k] = str(value)
            report["results"].append(row)
            print(
                json.dumps(
                    {
                        "completed": len(report["results"]),
                        "selected": len(sources),
                        "cost_usd": float(journal.cost),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        except Exception as exc:
            report["state"] = (
                "budget_stopped" if isinstance(exc, BudgetStop) else "stopped_for_error"
            )
            report["errors"].append(
                {
                    "product_id": source["product_id"],
                    "type": type(exc).__name__,
                    "message": str(exc) if isinstance(exc, BudgetStop) else None,
                }
            )
            break
        finally:
            report.update(
                completed=len(report["results"]),
                estimated_cost_usd=float(journal.cost),
                estimated_cost_incomplete=any(
                    r["state"] not in {"received", "rejected"} for r in journal.records.values()
                ),
                calls_by_stage=dict(Counter(r["stage"] for r in journal.records.values())),
                elapsed_this_invocation_seconds=round(monotonic() - started, 2),
            )
            save_json(manifest, report)
    if len(report["results"]) == len(sources):
        report["state"] = "completed"
    report["summary"] = {
        field: sum(bool(r["attributes"].get(field, {}).get("value")) for r in report["results"])
        for field in ("category", "subcategory", "brand", "content")
    }
    report["cost_per_product_usd"] = (
        float(journal.cost / len(report["results"])) if report["results"] else None
    )
    save_json(manifest, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--budget-usd", type=float, default=0.10)
    parser.add_argument("--max-proposals", type=int, default=10)
    parser.add_argument("--effort", choices=("none", "low"), default="none")
    args = parser.parse_args()
    if args.execute and not settings.openai_api_key:
        parser.error("OPENAI_API_KEY is required to execute")
    report = run(
        json.loads(args.snapshot.read_text(encoding="utf-8")),
        args.output_dir,
        execute=args.execute,
        limit=args.limit,
        budget=args.budget_usd,
        effort=args.effort,
        max_proposals=args.max_proposals,
    )
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in {"results", "signature"}},
            ensure_ascii=False,
            indent=2,
        )
    )
    if report["state"] not in {"completed", "dry_run"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
