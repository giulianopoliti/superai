"""Offline procurement telemetry reader; no application imports, network or DB access.

Missing metrics remain null. Source files are independent capture scopes: correlated
logs belong in one file; artifacts are never silently added to those same logs.
"""

import argparse
import json
import math
import re
import sys
from collections import defaultdict
from hashlib import sha256
from pathlib import Path

IDS = {
    "catalog_analysis": "request_id",
    "catalog_match": "request_id",
    "document_extraction": "extraction_id",
    "pack_review": "review_id",
}
MISSING_IDS = {"", "none", "unknown"}
PAIR = re.compile(r"(\w+)=([^\s]+)")


def reject_json_constant(value):
    raise ValueError(f"Non-finite JSON constant: {value}")


def finite_json_float(value):
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("Non-finite JSON number")
    return parsed


def number(value, *, integer=False):
    """Only finite, nonnegative measurements, never bool or numeric placeholders."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    if not math.isfinite(result) or result < 0 or (integer and not result.is_integer()):
        return None
    return int(result) if integer else result


def parse_log(text):
    records = []
    for line_no, line in enumerate(text.splitlines(), 1):
        match = re.search(
            r"\b(catalog_analysis|catalog_match|document_extraction|pack_review)\s+", line
        )
        if match:
            fields = dict(PAIR.findall(line[match.end() :]))
            records.append({"family": match[1], **fields, "line": line_no})
    return records


def artifact_records(data):
    """Read existing extraction metadata and shadow decisions, or explicit v1 events."""
    if not isinstance(data, dict):
        raise ValueError("Artifact must be an object")
    if data.get("schema_version") in {"procurement-benchmark-v1", "procurement-benchmark-v2"}:
        events = data.get("events")
        if not isinstance(events, list) or not all(isinstance(e, dict) for e in events):
            raise ValueError("events must be a list of objects")
        return events
    records = []
    # Public API response, saved document metadata, or a standalone AnalysisLedger.
    metadata = data.get("metadata", data.get("document_metadata", {}))
    if isinstance(data.get("extraction"), dict):
        metadata = data["extraction"].get("document_metadata", metadata)
    imported = data.get("import_result", {})
    if isinstance(imported, dict) and isinstance(imported.get("document"), dict):
        metadata = imported["document"].get("metadata", metadata)
    if not isinstance(metadata, dict):
        metadata = {}
    ledger = data.get("analysis_ledger", metadata.get("analysis_ledger"))
    if ledger is None and all(key in data for key in ("run_id", "business_id", "blocks", "calls")):
        ledger = data
    summary = data.get("analysis", metadata.get("analysis"))
    if isinstance(ledger, dict):
        records.extend(ledger_records(ledger))
    if isinstance(summary, dict):
        records.append(
            {
                **summary,
                "family": "analysis",
                "stage": "completed",
                "analysis_method": summary.get("method"),
                "business_id": ledger.get("business_id") if isinstance(ledger, dict) else None,
            }
        )
    extraction = data.get("extraction", data)
    if (
        isinstance(extraction, dict)
        and isinstance(extraction.get("document_metadata"), dict)
        and any(
            key in extraction["document_metadata"]
            for key in (
                "extraction_id",
                "initial_provider_calls",
                "retry_provider_calls",
                "pack_review_provider_calls",
                "duration_seconds",
            )
        )
    ):
        meta = extraction["document_metadata"]
        records.append(
            {
                "family": "document_extraction",
                "stage": "completed",
                "extraction_id": meta.get("extraction_id", "artifact"),
                "duration_ms": _milliseconds(meta.get("duration_seconds")),
                "initial_calls": meta.get("initial_provider_calls"),
                "retry_calls": meta.get("retry_provider_calls"),
                "failures": len(meta["provider_errors"])
                if isinstance(meta.get("provider_errors"), list)
                else None,
            }
        )
        if any(key.startswith("pack_review_") for key in meta):
            records.append(
                {
                    "family": "pack_review",
                    "review_id": "artifact",
                    "stage": "completed",
                    "duration_ms": _milliseconds(meta.get("pack_review_duration_seconds")),
                    "provider_calls": meta.get("pack_review_provider_calls"),
                    "target_count": meta.get("pack_review_count"),
                    "pending_count": meta.get("pack_needs_review_count"),
                }
            )
    if isinstance(data.get("decisions"), list):
        for index, decision in enumerate(data["decisions"]):
            if not isinstance(decision, dict):
                raise ValueError("Invalid shadow decision")
            records.append(
                {
                    "family": "jev_shadow",
                    "run_id": data.get("run_id", "artifact"),
                    "stage": "decision",
                    "event_id": str(index),
                    "input_tokens": decision.get("input_tokens"),
                    "output_tokens": decision.get("output_tokens"),
                    "error_type": "shadow_error" if decision.get("error") else None,
                    "model": decision.get("model_version"),
                }
            )
    return records


def ledger_records(ledger):
    """Adapt the published AnalysisLedger, not an alternate application DTO."""
    context = {
        "family": "analysis",
        "run_id": ledger.get("run_id"),
        "business_id": ledger.get("business_id"),
        "analysis_method": ledger.get("method"),
    }
    records = []
    calls = ledger.get("calls", [])
    if not isinstance(calls, list):
        raise ValueError("Invalid ledger calls")
    for call in calls:
        if not isinstance(call, dict):
            raise ValueError("Invalid ledger call")
        records.append({**context, **call, "stage": "call"})
    raw_blocks = ledger.get("blocks", [])
    if not isinstance(raw_blocks, list) or not all(isinstance(row, dict) for row in raw_blocks):
        raise ValueError("Invalid ledger blocks")
    if any(
        not isinstance(row.get("block_id"), str)
        or not row["block_id"]
        or not isinstance(row.get("derived_offer_ids", []), list)
        for row in raw_blocks
    ):
        raise ValueError("Invalid ledger block IDs/offers")
    by_id = {row.get("block_id"): row for row in raw_blocks if isinstance(row.get("block_id"), str)}
    pending_ids = {
        row.get("block_id")
        for row in ledger.get("pending_blocks", [])
        if isinstance(row, dict) and isinstance(row.get("block_id"), str)
    }
    links = defaultdict(list)
    for block in raw_blocks:
        if isinstance(block.get("supersedes_block_id"), str):
            links[block["supersedes_block_id"]].append(block)
    for block in raw_blocks:
        record = {
            **context,
            **block,
            "stage": "block",
            "status": "pending" if block.get("block_id") in pending_ids else "represented",
        }
        successors = links.get(block.get("block_id"), [])
        if successors:
            successor = successors[0]
            record["superseded_by"] = successor.get("block_id")
            usable_successor = True
            if "interpretations" in ledger:
                usable_successor = any(
                    isinstance(offer, dict)
                    and offer.get("block_id") == successor.get("block_id")
                    and isinstance(offer.get("raw_name"), str)
                    and offer["raw_name"].strip()
                    and number(offer.get("offer_price")) is not None
                    and number(offer.get("offer_price")) > 0
                    for offer in ledger["interpretations"]
                )
            record["association_validated"] = (
                len(successors) == 1
                and successor.get("run_id") == block.get("run_id") == ledger.get("run_id")
                and successor.get("page_number") == block.get("page_number")
                and successor.get("reading_id") != block.get("reading_id")
                and bool(successor.get("derived_offer_ids"))
                and not successor.get("reason_codes")
                and usable_successor
            )
        # The explicit backend supersedes relation declares association; no fuzzy matching.
        ancestor, visited = block, set()
        while isinstance(ancestor.get("supersedes_block_id"), str):
            previous_id = ancestor["supersedes_block_id"]
            if previous_id in visited or previous_id not in by_id:
                break
            visited.add(previous_id)
            ancestor = by_id[previous_id]
        record["lineage_id"] = ancestor.get("block_id")
        records.append(record)
    for offer in ledger.get("interpretations", []):
        if not isinstance(offer, dict):
            raise ValueError("Invalid ledger interpretation")
        records.append({**context, **offer, "stage": "interpretation"})
    return records


def _milliseconds(value):
    parsed = number(value)
    return parsed * 1000 if parsed is not None else None


def _unique_metric(records, key, warnings, *, integer=False):
    values = [number(row.get(key), integer=integer) for row in records if row.get(key) is not None]
    if any(value is None for value in values):
        warnings.append(f"Invalid {key}")
        return None
    distinct = set(values)
    if len(distinct) > 1:
        warnings.append(f"Conflicting {key}")
        return None
    return next(iter(distinct), None)


def normalize_event(row):
    """Normalize local telemetry, without interpreting document text or provider output."""
    row = dict(row)
    if row.get("analysis_run_id") is not None:
        row.setdefault("run_id", row["analysis_run_id"])
    if row.get("analysis_method") is not None:
        row.setdefault("method", row["analysis_method"])
    if row.get("duration_seconds") is not None and "duration_ms" not in row:
        row["duration_ms"] = _milliseconds(row["duration_seconds"])
    if isinstance(row.get("usage"), dict):
        for key in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cached_input_tokens",
            "reasoning_output_tokens",
        ):
            row.setdefault(key, row["usage"].get(key))
        for detail, key, target in (
            ("input_tokens_details", "cached_tokens", "cached_input_tokens"),
            ("output_tokens_details", "reasoning_tokens", "reasoning_output_tokens"),
        ):
            details = row["usage"].get(detail)
            if isinstance(details, dict) and row.get(target) is None:
                row[target] = details.get(key)
    if isinstance(row.get("cost"), dict):
        cost = row["cost"]
        if isinstance(cost.get("estimated"), bool):
            kind = "estimated_cost" if cost["estimated"] else "observed_cost"
            row.setdefault(
                kind,
                {
                    "amount": cost.get("amount"),
                    "currency": cost.get("currency"),
                    "source": cost.get("tariff_id"),
                },
            )
    if row.get("safe_error") or row.get("status") in {"error", "refusal", "incomplete"}:
        row.setdefault("error_type", row.get("status", "provider_error"))
    return row


def _deduplicate(rows, field, warnings):
    """Conflicting copies are excluded from metrics rather than arbitrarily trusted."""
    groups, missing = defaultdict(list), []
    for row in rows:
        identifier = row.get(field)
        if not isinstance(identifier, str) or not identifier:
            missing.append(row)
        else:
            groups[identifier].append(row)
    result = []
    for identifier, copies in groups.items():
        canonical = [
            {key: value for key, value in row.items() if key not in {"event_id", "line"}}
            for row in copies
        ]
        if any(copy != canonical[0] for copy in canonical[1:]):
            warnings.append(f"Conflicting {field}: {identifier}; excluded")
        else:
            result.append(copies[0])
    if missing:
        warnings.append(f"Missing {field}: {len(missing)} records; excluded")
    return result


def block_metrics(records, warnings):
    blocks = _deduplicate([row for row in records if row["stage"] == "block"], "block_id", warnings)
    if not blocks:
        return {
            "current": None,
            "historical": None,
            "pending_current": None,
            "derived_offers_current": None,
            "current_block_ids": [],
        }
    by_id = {row["block_id"]: row for row in blocks}
    retired = set()
    for row in blocks:
        target_id = row.get("superseded_by")
        if target_id is None:
            continue
        target = by_id.get(target_id) if isinstance(target_id, str) else None
        valid = (
            row.get("association_validated") is True
            and target is not None
            and target is not row
            and row.get("lineage_id")
            and row["lineage_id"] == target.get("lineage_id")
            and row.get("page_number") is not None
            and row["page_number"] == target.get("page_number")
        )
        # Reject cycles and chains ending in a block outside this run.
        visited = {row["block_id"]}
        cursor = target
        while valid and cursor is not None:
            if cursor["block_id"] in visited:
                valid = False
                break
            visited.add(cursor["block_id"])
            next_id = cursor.get("superseded_by")
            if next_id is None:
                break
            next_row = by_id.get(next_id) if isinstance(next_id, str) else None
            valid = (
                cursor.get("association_validated") is True
                and next_row is not None
                and next_row.get("lineage_id") == row["lineage_id"]
                and next_row.get("page_number") == row["page_number"]
            )
            cursor = next_row
        if valid:
            retired.add(row["block_id"])
        else:
            warnings.append(f"Unverified supersession: {row['block_id']}; remains current")
    current = [row for row in blocks if row["block_id"] not in retired]
    offers = {
        offer
        for row in current
        for offer in row.get("derived_offer_ids", [])
        if isinstance(offer, str)
    }
    return {
        "current": len(current),
        "historical": len(retired),
        "versions_observed": len(blocks),
        "current_block_ids": sorted(row["block_id"] for row in current),
        "pending_current": sum(row.get("status") == "pending" for row in current),
        "derived_offers_current": len(offers),
    }


def evaluation_metrics(records, warnings):
    """Evaluate explicitly supplied labels; never use predictions as ground truth."""
    rows = _deduplicate(
        [row for row in records if row["stage"] == "evaluation"], "offer_id", warnings
    )
    result = {}
    for axis in ("identity", "price"):
        labeled = [
            row
            for row in rows
            if isinstance(row.get("expected"), dict)
            and axis in row["expected"]
            and row.get("label_provenance") in {"synthetic", "human_labeled"}
        ]
        correct = sum(
            isinstance(row.get("predicted"), dict)
            and axis in row["predicted"]
            and row["predicted"][axis] == row["expected"][axis]
            for row in labeled
        )
        provenances = sorted({row["label_provenance"] for row in labeled})
        result[axis] = {
            "labeled": len(labeled),
            "correct": correct if labeled else None,
            "accuracy": correct / len(labeled) if len(provenances) == 1 else None,
            "label_provenance": provenances,
        }
        if len(provenances) > 1:
            warnings.append(f"Mixed label provenance for {axis}; accuracy withheld")
    return result


def offer_state_metrics(records, blocks, warnings):
    rows = _deduplicate(
        [row for row in records if row["stage"] == "interpretation"], "offer_id", warnings
    )
    current_ids = set(blocks["current_block_ids"])
    if current_ids:
        rows = [row for row in rows if row.get("block_id") in current_ids]
    return {
        axis: {state: sum(row.get(f"{axis}_status") == state for row in rows) for state in states}
        if rows
        else None
        for axis, states in (
            ("identity", ("confirmed", "pending", "no_match")),
            ("price", ("confirmed", "pending")),
        )
    }


def summarize(records):
    records = [normalize_event(row) for row in records]
    warnings = []
    stages = defaultdict(list)
    for row in records:
        stages[row["stage"]].append(row)
    completed = stages.get("completed", [])
    total_key = (
        "total_duration_ms"
        if any("total_duration_ms" in row for row in completed)
        else "duration_ms"
    )
    duration = _unique_metric(completed, total_key, warnings)
    failures = [row for row in records if row["stage"].endswith("failed")]
    calls = None
    initial = _unique_metric(completed, "initial_calls", warnings, integer=True)
    retry = _unique_metric(completed, "retry_calls", warnings, integer=True)
    if initial is not None and retry is not None:
        calls = initial + retry
    elif any("provider_calls" in row for row in completed):
        calls = _unique_metric(completed, "provider_calls", warnings, integer=True)
    starts = stages.get("provider_started", [])
    # Started calls are a lower bound for incomplete captures, not a total.
    errors = _unique_metric(completed, "failures", warnings, integer=True)
    explicit_calls = [row for row in records if row["stage"] in {"call", "decision"}]
    v2_calls = [row for row in explicit_calls if row.get("call_id") is not None]
    call_record_count = len(explicit_calls)
    if v2_calls:
        legacy_calls = [row for row in explicit_calls if row.get("call_id") is None]
        call_record_count = len(
            {row["call_id"] for row in v2_calls if isinstance(row["call_id"], str)}
        ) + len(legacy_calls)
        explicit_calls = _deduplicate(v2_calls, "call_id", warnings) + legacy_calls
    usage = {}
    for field in (
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    ):
        values = [number(row.get(field), integer=True) for row in explicit_calls]
        present = [value for value in values if value is not None]
        usage[field] = {
            "known_sum": sum(present) if present else None,
            "records_with_usage": len(present),
            "records": call_record_count,
            "complete": bool(values) and len(present) == call_record_count,
        }
    costs = {}
    for kind in ("observed_cost", "estimated_cost"):
        amounts = defaultdict(list)
        for row in explicit_calls:
            entry = row.get(kind)
            if entry is None:
                continue
            if not isinstance(entry, dict):
                warnings.append(f"Invalid {kind}")
                continue
            amount = number(entry.get("amount"))
            if (
                amount is None
                or not isinstance(entry.get("currency"), str)
                or not entry["currency"]
                or not isinstance(entry.get("source"), str)
                or not entry["source"]
            ):
                warnings.append(f"Invalid {kind}: amount, currency and source required")
                continue
            amounts[entry["currency"]].append(amount)
        costs[kind] = {
            "known_subtotals": {key: sum(value) for key, value in amounts.items()},
            "records_with_cost": sum(map(len, amounts.values())),
            "complete": bool(explicit_calls)
            and sum(map(len, amounts.values())) == call_record_count,
        }
    blocks = block_metrics(records, warnings)
    offer_states = offer_state_metrics(records, blocks, warnings)
    evaluation = evaluation_metrics(records, warnings)
    versions = {
        key: sorted({row[key] for row in records if isinstance(row.get(key), str)})
        for key in (
            "reading_id",
            "model",
            "prompt_version",
            "schema_version",
            "policy_version",
            "catalog_version",
            "feedback_version",
        )
    }
    return {
        "duration_ms": duration,
        "failure_elapsed_ms": _unique_metric(failures, "total_duration_ms", warnings),
        "provider_calls": calls,
        "provider_starts_observed": len(starts) if starts else None,
        "provider_failures": errors,
        "failure_events_observed": len(failures),
        "decision_errors_observed": sum(bool(row.get("error_type")) for row in explicit_calls),
        "pending_count": _unique_metric(completed, "pending_count", warnings, integer=True),
        "usage": usage,
        "cost": costs,
        "calls_observed_unique": call_record_count if v2_calls else None,
        "calls_excluded_conflicting": call_record_count - len(explicit_calls) if v2_calls else 0,
        "blocks": blocks,
        "evaluation": evaluation,
        "offer_states": offer_states,
        "versions": versions,
        "stages": {
            stage: {
                "records": len(rows),
                "duration_ms": _unique_metric(rows, "duration_ms", warnings),
                "observations": [
                    {
                        key: row[key]
                        for key in (
                            "event_id",
                            "call_id",
                            "reading_id",
                            "analysis_method",
                            "prompt_version",
                            "schema_version",
                            "policy_version",
                            "provider",
                            "phase",
                            "pages",
                            "page",
                            "model",
                            "status",
                            "duration_ms",
                            "input_tokens",
                            "output_tokens",
                            "observed_cost",
                            "estimated_cost",
                        )
                        if key in row
                    }
                    for row in rows
                ],
            }
            for stage, rows in sorted(stages.items())
        },
        "warnings": sorted(set(warnings)),
    }


def analyze(paths, *, cohort="unclassified", provenance="unknown"):
    report = {
        "schema_version": "procurement-benchmark-report-v1",
        "cohort": cohort,
        "provenance": provenance,
        "sources": [],
        "runs": [],
        "diagnostics": [],
    }
    seen_paths = set()
    for path in paths:
        path = Path(path).resolve()
        if path in seen_paths:
            report["diagnostics"].append(f"Repeated input skipped: {path}")
            continue
        seen_paths.add(path)
        raw = path.read_bytes()
        source = str(path)
        report["sources"].append({"path": source, "sha256": sha256(raw).hexdigest()})
        try:
            content = raw.decode("utf-8-sig")
            if path.suffix.lower() == ".json":
                records = artifact_records(
                    json.loads(
                        content, parse_constant=reject_json_constant, parse_float=finite_json_float
                    )
                )
            elif path.suffix.lower() == ".jsonl":
                records = [
                    json.loads(
                        line, parse_constant=reject_json_constant, parse_float=finite_json_float
                    )
                    for line in content.splitlines()
                    if line.strip()
                ]
            else:
                records = parse_log(content)
        except (ValueError, UnicodeError) as exc:
            report["diagnostics"].append(f"Unreadable artifact: {source}: {exc}")
            continue
        groups = defaultdict(list)
        seen_events = {}
        for index, row in enumerate(records, 1):
            if not isinstance(row, dict):
                report["diagnostics"].append(f"Invalid record: {source}:{index}")
                continue
            row = normalize_event(row)
            family = row.get("family")
            if not isinstance(family, str) or not family:
                report["diagnostics"].append(f"Missing family: {source}:{index}")
                continue
            identifier = row.get(IDS.get(family, "run_id"))
            if (
                not isinstance(identifier, str)
                or identifier.lower() in MISSING_IDS
                or not isinstance(row.get("stage"), str)
                or not row["stage"]
            ):
                report["diagnostics"].append(f"Missing run/stage: {source}:{index}")
                continue
            method = row.get("method")
            business_id = row.get("business_id")
            if any(
                value is not None and not isinstance(value, str) for value in (method, business_id)
            ):
                report["diagnostics"].append(f"Invalid method/tenant: {source}:{index}")
                continue
            key = (family, identifier, method or "", business_id or "")
            if row.get("event_id") is not None:
                event_key = (*key, str(row["event_id"]))
                if event_key in seen_events:
                    report["diagnostics"].append(f"Duplicate event rejected: {source}:{index}")
                    continue
                seen_events[event_key] = row
            groups[key].append(row)
        for (family, identifier, method, business_id), rows in sorted(groups.items()):
            links = sorted(
                {
                    row["extraction_id"]
                    for row in rows
                    if family == "catalog_analysis"
                    and isinstance(row.get("extraction_id"), str)
                    and row["extraction_id"].lower() not in MISSING_IDS
                }
            )
            report["runs"].append(
                {
                    "source": source,
                    "family": family,
                    "run_id": identifier,
                    "analysis_method": method or None,
                    "business_id": business_id or None,
                    "extraction_links": links,
                    **summarize(rows),
                }
            )
        if not groups:
            report["diagnostics"].append(f"No recognized telemetry: {source}")
    return report


def main():
    parser = argparse.ArgumentParser(description="Analiza telemetría local sin API ni DB")
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument(
        "--cohort",
        choices=(
            "openai_current",
            "selective",
            "historical_dual",
            "unclassified",
            "openai_structured",
            "openai_blocks_jev",
        ),
        default="unclassified",
    )
    parser.add_argument("--provenance", choices=("synthetic", "real", "unknown"), default="unknown")
    args = parser.parse_args()
    try:
        report = analyze(args.inputs, cohort=args.cohort, provenance=args.provenance)
    except OSError as exc:
        parser.error(str(exc))
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2, allow_nan=False)
    print()


if __name__ == "__main__":
    main()
