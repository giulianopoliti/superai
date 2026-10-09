"""Synthetic telemetry fixtures: these are not human-labeled offer documents."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.benchmark_procurement import analyze, artifact_records, number, parse_log, summarize

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "procurement_analysis"


class BenchmarkTests(unittest.TestCase):
    def analyze_text(self, text, suffix=".log"):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ("capture" + suffix)
            path.write_text(text, encoding="utf-8")
            return analyze([path], provenance="synthetic")

    def test_interleaved_requests_keep_families_and_executions_separate(self):
        report = self.analyze_text(
            "\n".join(
                [
                    "INFO catalog_analysis request_id=a stage=started provider=openai",
                    "INFO catalog_analysis request_id=b stage=started provider=openai",
                    "document_extraction extraction_id=x stage=completed duration_ms=900 "
                    "initial_calls=2 retry_calls=1 failures=1",
                    "catalog_analysis request_id=b stage=completed duration_ms=2200",
                    "catalog_analysis request_id=a stage=document_extracted "
                    "duration_ms=1000 extraction_id=x",
                    "catalog_analysis request_id=a stage=completed duration_ms=1600",
                    "catalog_match request_id=a stage=completed "
                    "duration_ms=20 total_duration_ms=100",
                    "pack_review review_id=a stage=completed duration_ms=300 pending_count=1",
                ]
            )
        )
        runs = {(r["family"], r["run_id"]): r for r in report["runs"]}
        self.assertEqual(len(runs), 5)
        self.assertEqual(runs["catalog_analysis", "a"]["duration_ms"], 1600)
        self.assertEqual(runs["catalog_analysis", "a"]["extraction_links"], ["x"])
        self.assertEqual(runs["catalog_analysis", "b"]["duration_ms"], 2200)
        self.assertEqual(runs["catalog_match", "a"]["duration_ms"], 100)
        self.assertEqual(runs["document_extraction", "x"]["provider_calls"], 3)
        self.assertIsNone(runs["pack_review", "a"]["provider_calls"])

    def test_item_rows_are_not_pack_api_calls_or_wall_time(self):
        rows = parse_log(
            "\n".join(
                [
                    "pack_review review_id=x stage=item_completed item_index=1 "
                    "duration_ms=100 status=reviewed",
                    "pack_review review_id=x stage=item_completed item_index=2 "
                    "duration_ms=1 status=reviewed",
                    "pack_review review_id=x stage=completed duration_ms=110 "
                    "target_count=2 pending_count=0",
                ]
            )
        )
        result = summarize(rows)
        self.assertEqual(result["duration_ms"], 110)
        self.assertIsNone(result["provider_calls"])
        self.assertIsNone(result["usage"]["input_tokens"]["known_sum"])
        self.assertFalse(result["cost"]["observed_cost"]["complete"])

    def test_failure_elapsed_is_not_success_total(self):
        rows = parse_log(
            "catalog_analysis request_id=x stage=document_processing_failed "
            "error_type=TimeoutError total_duration_ms=60000"
        )
        result = summarize(rows)
        self.assertIsNone(result["duration_ms"])
        self.assertEqual(result["failure_elapsed_ms"], 60000)
        self.assertEqual(result["failure_events_observed"], 1)

    def test_missing_and_invalid_measurements_are_not_zero(self):
        for value in (None, "unknown", -1, "NaN", "inf", True, "1.5"):
            self.assertIsNone(number(value, integer=True))
        self.assertEqual(number(0, integer=True), 0)
        result = summarize([{"stage": "completed", "duration_ms": "NaN", "initial_calls": 2}])
        self.assertIsNone(result["duration_ms"])
        self.assertIsNone(result["provider_calls"])
        self.assertIn("Invalid duration_ms", result["warnings"])

    def test_conflicting_totals_are_flagged(self):
        result = summarize(
            [{"stage": "completed", "duration_ms": 1}, {"stage": "completed", "duration_ms": 2}]
        )
        self.assertIsNone(result["duration_ms"])
        self.assertIn("Conflicting duration_ms", result["warnings"])

    def test_artifact_metadata_counts_and_seconds(self):
        rows = artifact_records(
            {
                "document_metadata": {
                    "extraction_id": "x",
                    "duration_seconds": 1.2,
                    "initial_provider_calls": 2,
                    "retry_provider_calls": 0,
                    "provider_errors": [],
                    "pack_review_provider_calls": 1,
                    "pack_review_count": 8,
                    "pack_review_duration_seconds": 0.5,
                    "pack_needs_review_count": 2,
                }
            }
        )
        self.assertEqual(summarize(rows[:1])["provider_calls"], 2)
        self.assertEqual(summarize(rows[:1])["duration_ms"], 1200)
        self.assertEqual(summarize(rows[1:])["provider_calls"], 1)
        self.assertEqual(summarize(rows[1:])["pending_count"], 2)

    def test_shadow_usage_partial_and_errors(self):
        rows = artifact_records(
            {
                "decisions": [
                    {"input_tokens": 10, "output_tokens": 2, "model_version": "fixed"},
                    {"error": "timeout", "input_tokens": None, "output_tokens": None},
                ]
            }
        )
        result = summarize(rows)
        self.assertEqual(result["usage"]["input_tokens"]["known_sum"], 10)
        self.assertFalse(result["usage"]["input_tokens"]["complete"])
        self.assertEqual(result["decision_errors_observed"], 1)
        self.assertIsNone(result["provider_calls"])

    def test_cost_observed_estimated_missing_and_currency_are_separate(self):
        result = summarize(
            [
                {
                    "stage": "call",
                    "observed_cost": {"amount": 0.1, "currency": "USD", "source": "billing-export"},
                },
                {
                    "stage": "call",
                    "estimated_cost": {
                        "amount": 2,
                        "currency": "ARS",
                        "source": "explicit-rate-snapshot",
                    },
                },
                {"stage": "call", "observed_cost": {"amount": 0, "currency": "USD"}},
            ]
        )
        self.assertEqual(result["cost"]["observed_cost"]["known_subtotals"], {"USD": 0.1})
        self.assertEqual(result["cost"]["estimated_cost"]["known_subtotals"], {"ARS": 2})
        self.assertFalse(result["cost"]["observed_cost"]["complete"])
        self.assertTrue(result["warnings"])

    def test_jsonl_dedup_and_missing_identity(self):
        event = {
            "family": "benchmark",
            "run_id": "x",
            "stage": "call",
            "event_id": "1",
            "input_tokens": 10,
            "output_tokens": 2,
        }
        report = self.analyze_text(
            "\n".join(map(json.dumps, [event, event, {"stage": "call"}])), ".jsonl"
        )
        self.assertEqual(report["runs"][0]["usage"]["input_tokens"]["known_sum"], 10)
        self.assertEqual(len(report["diagnostics"]), 2)

    def test_sources_do_not_merge_or_count_repeated_path(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ("first.log", "second.log")]
            for path in paths:
                path.write_text("catalog_analysis request_id=x stage=completed duration_ms=1")
            report = analyze([*paths, paths[0]])
        self.assertEqual(len(report["runs"]), 2)
        self.assertEqual(len(report["sources"]), 2)
        self.assertEqual(len(report["diagnostics"]), 1)

    def test_empty_and_unrecognized_artifacts_are_diagnostic(self):
        self.assertTrue(self.analyze_text("", ".json")["diagnostics"])
        report = self.analyze_text(json.dumps({"baseline": {}, "metrics": {}}), ".json")
        self.assertEqual(report["runs"], [])
        self.assertTrue(report["diagnostics"])

    def test_invalid_family_is_diagnostic_not_parser_crash(self):
        report = self.analyze_text(
            json.dumps({"family": [], "run_id": "x", "stage": "call"}), ".jsonl"
        )
        self.assertEqual(report["runs"], [])
        self.assertTrue(report["diagnostics"])

    def test_nonfinite_json_cannot_break_report_serialization(self):
        report = self.analyze_text(
            '{"family":"benchmark","run_id":"x","stage":"call","duration_ms":NaN}',
            ".jsonl",
        )
        self.assertEqual(report["runs"], [])
        self.assertIn("Non-finite", report["diagnostics"][0])
        json.dumps(report, allow_nan=False)

    def test_cli_writes_machine_readable_report_without_application_imports(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.log"
            path.write_text("catalog_analysis request_id=x stage=completed duration_ms=100")
            script = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_procurement.py"
            result = subprocess.run(
                [sys.executable, "-S", str(script), str(path), "--provenance", "synthetic"],
                capture_output=True,
                text=True,
                check=True,
            )
        report = json.loads(result.stdout)
        self.assertEqual(report["runs"][0]["duration_ms"], 100)
        self.assertEqual(report["provenance"], "synthetic")

    def test_both_methods_and_nullable_usage_from_synthetic_capture(self):
        report = analyze([FIXTURES / "telemetry.json"], provenance="synthetic")
        runs = {row["analysis_method"]: row for row in report["runs"]}
        a, b = runs["openai_structured"], runs["openai_blocks_jev"]
        self.assertEqual(a["usage"]["input_tokens"]["known_sum"], 100)
        self.assertEqual(a["usage"]["cached_input_tokens"]["known_sum"], 40)
        self.assertEqual(a["usage"]["total_tokens"]["known_sum"], 120)
        self.assertEqual(b["calls_observed_unique"], 2)
        self.assertFalse(b["usage"]["input_tokens"]["complete"])
        self.assertEqual(b["decision_errors_observed"], 1)
        self.assertEqual(a["evaluation"]["identity"]["accuracy"], 1)
        self.assertEqual(a["evaluation"]["price"]["accuracy"], 0)
        self.assertEqual(a["blocks"]["current"], 2)
        self.assertEqual(a["blocks"]["historical"], 1)
        self.assertEqual(a["blocks"]["derived_offers_current"], 2)
        self.assertEqual(a["blocks"]["pending_current"], 1)

    def test_duplicate_call_does_not_add_usage_or_cost_from_versions(self):
        call = {
            "stage": "call",
            "call_id": "c1",
            "reading_id": "read-1",
            "input_tokens": 100,
            "output_tokens": 20,
            "cost": {
                "amount": "0.10",
                "currency": "USD",
                "tariff_id": "synthetic-tariff",
                "estimated": True,
            },
        }
        result = summarize([call, {**call, "event_id": "copy-in-older-version"}])
        self.assertEqual(result["calls_observed_unique"], 1)
        self.assertEqual(result["usage"]["input_tokens"]["known_sum"], 100)
        self.assertEqual(result["cost"]["estimated_cost"]["known_subtotals"], {"USD": 0.1})
        self.assertEqual(result["cost"]["observed_cost"]["known_subtotals"], {})

    def test_conflicting_call_copies_are_excluded(self):
        result = summarize(
            [
                {"stage": "call", "call_id": "c1", "input_tokens": 100},
                {"stage": "call", "call_id": "c1", "input_tokens": 200},
            ]
        )
        self.assertIsNone(result["usage"]["input_tokens"]["known_sum"])
        self.assertTrue(any("Conflicting call_id" in warning for warning in result["warnings"]))

    def test_unverified_supersession_keeps_old_pending_and_repeated_blocks(self):
        rows = [
            {
                "stage": "block",
                "block_id": "old",
                "page_number": 1,
                "lineage_id": "l",
                "status": "pending",
                "superseded_by": "new",
            },
            {"stage": "block", "block_id": "new", "page_number": 1, "lineage_id": "l"},
        ]
        result = summarize(rows)
        self.assertEqual(result["blocks"]["current"], 2)
        self.assertEqual(result["blocks"]["historical"], 0)
        self.assertEqual(result["blocks"]["pending_current"], 1)
        self.assertTrue(result["warnings"])

    def test_supersession_cannot_cross_pages_or_cycle(self):
        rows = [
            {
                "stage": "block",
                "block_id": "old",
                "page_number": 1,
                "lineage_id": "l",
                "superseded_by": "new",
                "association_validated": True,
            },
            {"stage": "block", "block_id": "new", "page_number": 2, "lineage_id": "l"},
        ]
        self.assertEqual(summarize(rows)["blocks"]["historical"], 0)
        rows[1].update(page_number=1, superseded_by="old", association_validated=True)
        self.assertEqual(summarize(rows)["blocks"]["historical"], 0)

    def test_candidates_and_duplicate_evaluation_do_not_expand_denominator(self):
        evaluation = {
            "stage": "evaluation",
            "offer_id": "o1",
            "label_provenance": "synthetic",
            "expected": {"identity": "p1"},
            "predicted": {"identity": "p1"},
        }
        rows = [
            evaluation,
            evaluation,
            {"stage": "candidate", "offer_id": "o1"},
            {"stage": "candidate", "offer_id": "o1"},
        ]
        result = summarize(rows)
        self.assertEqual(result["evaluation"]["identity"]["labeled"], 1)
        self.assertIsNone(result["evaluation"]["price"]["accuracy"])

    def test_predictions_without_labels_have_no_quality_measurement(self):
        result = summarize(
            [
                {
                    "stage": "evaluation",
                    "offer_id": "o1",
                    "predicted": {"identity": "p1", "price": "1500"},
                }
            ]
        )
        self.assertIsNone(result["evaluation"]["identity"]["accuracy"])
        self.assertIsNone(result["evaluation"]["price"]["accuracy"])

    def test_reading_prompt_schema_versions_survive(self):
        result = summarize(
            [
                {
                    "stage": "call",
                    "call_id": "c1",
                    "reading_id": "read-2",
                    "model": "fake",
                    "prompt_version": "v2",
                    "schema_version": "wire2",
                }
            ]
        )
        self.assertEqual(result["versions"]["reading_id"], ["read-2"])
        self.assertEqual(result["versions"]["prompt_version"], ["v2"])
        self.assertEqual(result["versions"]["schema_version"], ["wire2"])

    def test_same_run_id_methods_and_tenants_do_not_merge(self):
        base = {"family": "analysis", "run_id": "r", "stage": "call", "call_id": "c"}
        rows = [
            {**base, "analysis_method": "openai_structured", "business_id": "a"},
            {**base, "analysis_method": "openai_blocks_jev", "business_id": "a"},
            {**base, "analysis_method": "openai_structured", "business_id": "b"},
        ]
        report = self.analyze_text("\n".join(map(json.dumps, rows)), ".jsonl")
        self.assertEqual(len(report["runs"]), 3)

    def test_published_ledger_adapter_handles_supersedes_and_summary(self):
        data = {
            "analysis": {
                "run_id": "r",
                "method": "openai_structured",
                "duration_seconds": 1.5,
                "usage": {"input_tokens": 999},
            },
            "analysis_ledger": {
                "run_id": "r",
                "business_id": "a",
                "method": "openai_structured",
                "pending_blocks": [],
                "blocks": [
                    {"block_id": "old", "run_id": "r", "reading_id": "initial", "page_number": 1},
                    {
                        "block_id": "new",
                        "run_id": "r",
                        "reading_id": "reread",
                        "page_number": 1,
                        "supersedes_block_id": "old",
                        "derived_offer_ids": ["o1"],
                    },
                ],
                "calls": [
                    {
                        "call_id": "c",
                        "run_id": "r",
                        "usage": {"input_tokens": 100, "output_tokens": 20},
                    }
                ],
            },
        }
        report = self.analyze_text(json.dumps(data), ".json")
        result = report["runs"][0]
        self.assertEqual(result["duration_ms"], 1500)
        self.assertEqual(result["usage"]["input_tokens"]["known_sum"], 100)
        self.assertEqual(result["blocks"]["current"], 1)
        self.assertEqual(result["blocks"]["historical"], 1)

    def test_conflicting_call_does_not_make_partial_usage_complete(self):
        result = summarize(
            [
                {"stage": "call", "call_id": "conflict", "input_tokens": 10},
                {"stage": "call", "call_id": "conflict", "input_tokens": 20},
                {"stage": "call", "call_id": "known", "input_tokens": 30},
            ]
        )
        self.assertEqual(result["usage"]["input_tokens"]["known_sum"], 30)
        self.assertFalse(result["usage"]["input_tokens"]["complete"])
        self.assertEqual(result["calls_observed_unique"], 2)
        self.assertEqual(result["calls_excluded_conflicting"], 1)

    def test_mixed_human_and_synthetic_labels_are_not_combined_accuracy(self):
        rows = [
            {
                "stage": "evaluation",
                "offer_id": "synthetic",
                "label_provenance": "synthetic",
                "expected": {"identity": "p"},
                "predicted": {"identity": "p"},
            },
            {
                "stage": "evaluation",
                "offer_id": "human",
                "label_provenance": "human_labeled",
                "expected": {"identity": "p"},
                "predicted": {"identity": "p"},
            },
        ]
        result = summarize(rows)
        self.assertIsNone(result["evaluation"]["identity"]["accuracy"])
        self.assertTrue(result["warnings"])

    def test_overflowing_json_numbers_are_diagnostic(self):
        report = self.analyze_text(
            '{"family":"analysis","run_id":"r","stage":"call","duration_ms":1e309}',
            ".jsonl",
        )
        self.assertEqual(report["runs"], [])
        self.assertTrue(report["diagnostics"])

    def test_malformed_ledger_calls_are_diagnostic(self):
        report = self.analyze_text(json.dumps({"analysis_ledger": {"calls": None}}), ".json")
        self.assertTrue(report["diagnostics"])

    def test_current_offer_states_are_independent_and_old_versions_are_excluded(self):
        blocks = [
            {
                "stage": "block",
                "block_id": "old",
                "page_number": 1,
                "lineage_id": "l",
                "superseded_by": "new",
                "association_validated": True,
            },
            {"stage": "block", "block_id": "new", "page_number": 1, "lineage_id": "l"},
        ]
        offers = [
            {
                "stage": "interpretation",
                "offer_id": "o-old",
                "block_id": "old",
                "identity_status": "pending",
                "price_status": "pending",
            },
            {
                "stage": "interpretation",
                "offer_id": "o-new",
                "block_id": "new",
                "identity_status": "confirmed",
                "price_status": "pending",
            },
        ]
        result = summarize(blocks + offers)
        self.assertEqual(
            result["offer_states"]["identity"], {"confirmed": 1, "pending": 0, "no_match": 0}
        )
        self.assertEqual(result["offer_states"]["price"], {"confirmed": 0, "pending": 1})

    def test_direct_v2_extraction_export_has_no_phantom_legacy_run(self):
        data = {
            "document_metadata": {
                "analysis": {"run_id": "r", "method": "openai_structured", "duration_seconds": 1},
                "analysis_ledger": {
                    "run_id": "r",
                    "business_id": "tenant",
                    "method": "openai_structured",
                    "calls": [],
                    "blocks": [],
                },
            }
        }
        report = self.analyze_text(json.dumps(data), ".json")
        self.assertEqual(len(report["runs"]), 1)
        self.assertEqual(report["runs"][0]["family"], "analysis")
        self.assertEqual(report["runs"][0]["duration_ms"], 1000)

    def test_degraded_reread_does_not_retire_valid_block_in_published_ledger(self):
        ledger = {
            "run_id": "r",
            "business_id": "tenant",
            "method": "openai_structured",
            "calls": [],
            "pending_blocks": [{"block_id": "new"}],
            "blocks": [
                {
                    "block_id": "old",
                    "run_id": "r",
                    "reading_id": "initial",
                    "page_number": 1,
                    "derived_offer_ids": ["o-old"],
                },
                {
                    "block_id": "new",
                    "run_id": "r",
                    "reading_id": "reread",
                    "page_number": 1,
                    "supersedes_block_id": "old",
                    "derived_offer_ids": ["o-new"],
                },
            ],
            "interpretations": [
                {"offer_id": "o-old", "block_id": "old", "raw_name": "Arroz", "offer_price": 1500},
                {"offer_id": "o-new", "block_id": "new", "raw_name": "Arroz", "offer_price": None},
            ],
        }
        result = summarize(artifact_records({"analysis_ledger": ledger}))
        self.assertEqual(result["blocks"]["current"], 2)
        self.assertEqual(result["blocks"]["historical"], 0)


if __name__ == "__main__":
    unittest.main()
