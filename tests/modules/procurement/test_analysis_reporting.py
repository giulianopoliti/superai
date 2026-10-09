from copy import deepcopy

from app.modules.procurement.analysis_reporting import summary_from_metadata


def metadata():
    return {
        "analysis": {
            "run_id": "run",
            "method": "openai_structured",
            "status": "partial",
            "page_count": 24,
            "observed_block_count": 55,
            "interpreted_offer_count": 49,
            "pending_block_count": 6,
            "unlinked_offer_count": 49,
        },
        "analysis_ledger": {
            "run_id": "run",
            "pages": [{"page_number": n, "initial_done": n <= 4} for n in range(1, 25)],
            "calls": [
                {
                    "phase": "initial",
                    "start_page": n,
                    "end_page": n + 1,
                    "status": "success" if n < 5 else "error",
                }
                for n in [1, 3, 5, 7]
            ],
            "decision_audit": [],
        },
    }


def test_vital_coverage_separates_read_attempted_failed_and_unattempted_without_writing():
    data = metadata()
    original = deepcopy(data)
    summary = summary_from_metadata(data)
    assert summary.read_page_count == 4
    assert summary.attempted_page_count == 8
    assert summary.failed_page_numbers == [5, 6, 7, 8]
    assert summary.unattempted_page_numbers == list(range(9, 25))
    assert summary.provider_call_count == 4
    assert summary.interpreted_offer_count == 49
    assert summary.pending_block_count == 6
    assert data == original


def test_historical_summary_without_ledger_does_not_invent_coverage():
    data = metadata()
    del data["analysis_ledger"]
    summary = summary_from_metadata(data)
    assert summary.read_page_count is None
    assert summary.provider_call_count is None
    assert summary.unattempted_page_numbers is None


def test_call_without_page_range_does_not_fabricate_unattempted_pages():
    data = metadata()
    data["analysis_ledger"]["calls"].append({"phase": "initial", "status": "error"})
    summary = summary_from_metadata(data)
    assert summary.read_page_count == 4
    assert summary.attempted_page_count is None
    assert summary.unattempted_page_numbers is None


def test_local_rejections_are_distinct_from_http_calls():
    data = metadata()
    data["analysis_ledger"]["decision_audit"] = [
        {"reason_codes": ["jev_request_limit"], "call_ids": []},
        {"reason_codes": ["deadline_exhausted"], "call_ids": []},
        {"reason_codes": ["jev_request_limit"], "call_ids": ["http"]},
    ]
    summary = summary_from_metadata(data)
    assert summary.provider_call_count == 4
    assert summary.local_rejection_count == 1


def test_mismatched_ledger_is_not_used_as_coverage_evidence():
    data = metadata()
    data["analysis_ledger"]["run_id"] = "other"
    assert summary_from_metadata(data).read_page_count is None
