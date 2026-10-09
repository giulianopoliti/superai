"""Read-only progress projection for current and historical analysis metadata."""

from app.modules.procurement.analysis_contracts import AnalysisSummary


def summary_from_metadata(metadata: dict) -> AnalysisSummary | None:
    stored = metadata.get("analysis")
    if not stored:
        return None
    summary = AnalysisSummary.model_validate(stored)
    ledger = metadata.get("analysis_ledger")
    if not isinstance(ledger, dict) or ledger.get("run_id") != summary.run_id:
        return summary
    pages, calls = ledger.get("pages"), ledger.get("calls")
    if not isinstance(pages, list) or not isinstance(calls, list):
        return summary
    read = {p.get("page_number") for p in pages if isinstance(p, dict) and p.get("initial_done")}
    read &= set(range(1, summary.page_count + 1))
    attempted = set()
    unknown_attempts = False
    for call in calls:
        if not isinstance(call, dict) or call.get("phase") not in {"initial", "reread"}:
            continue
        start, end = call.get("start_page"), call.get("end_page")
        if type(start) is int and type(end) is int and 1 <= start <= end <= summary.page_count:
            attempted.update(range(start, end + 1))
        else:
            unknown_attempts = True
    # A page with a result was necessarily attempted, even if old call metadata
    # omitted its page range. This does not infer attempts for unread pages.
    attempted |= read
    local_rejections = sum(
        any(
            code in {"jev_request_limit", "jev_payload_unpartitionable"}
            for code in entry.get("reason_codes", [])
        )
        and not entry.get("call_ids")
        for entry in ledger.get("decision_audit", [])
        if isinstance(entry, dict)
    )
    return summary.model_copy(
        update={
            "read_page_count": len(read),
            "attempted_page_count": None if unknown_attempts else len(attempted),
            "unattempted_page_numbers": None
            if unknown_attempts
            else sorted(set(range(1, summary.page_count + 1)) - attempted),
            "failed_page_numbers": None if unknown_attempts else sorted(attempted - read),
            "provider_call_count": len(calls),
            "local_rejection_count": summary.local_rejection_count
            if summary.local_rejection_count is not None
            else local_rejections,
        }
    )
