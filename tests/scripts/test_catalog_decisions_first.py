"""Offline regression tests for candidate coverage, recovery and paid call boundaries."""

from types import SimpleNamespace

import pytest

from app.modules.procurement.catalog_decisions_first import (
    family_result,
    prepare_options,
    questions_for,
)
from app.providers.catalog_decisions import CatalogDecisionResponse
from scripts.interpret_catalog_decisions_first import BudgetStop, CallJournal, Pilot, run


def source(name="CACAO MARCA 123 180 G"):
    return dict(
        product_id="p",
        business_id="a",
        original_name=name,
        brand=None,
        category="ALMACEN",
        unit_size=None,
        unit=None,
        external_product_id=None,
    )


def entry(identifier, attribute, label, parent_id=None):
    return dict(
        id=identifier,
        attribute=attribute,
        label=label,
        parent_id=parent_id,
        aliases=[],
        status="active",
    )


ENTRIES = [
    entry("c", "category", "Desayunos"),
    entry("s", "subcategory", "Cacao", "c"),
    entry("v", "variant", "MARCA 123"),
]


def response(questions, choose):
    answers = []
    for q in questions:
        choice = choose(q)
        answers.append(
            dict(
                name=q["name"],
                type="choice",
                choice=choice,
                confidence=0.99,
                probabilities=[
                    dict(value=c["value"], probability=float(c["value"] == choice))
                    for c in q["choices"]
                ],
            )
        )
    return dict(model="gpt-6-luna", usage=dict(input_tokens=100), answers=answers)


class Chooser:
    calls = 0
    on_response = None

    def __init__(self, *, category="c", subcategory="s"):
        self.category = category
        self.subcategory = subcategory

    def decide(self, data, questions):
        self.calls += 1

        def choose(q):
            if q["name"] == "category":
                if self.category in {"none", "unknown"}:
                    return q["choices"][-2 if self.category == "none" else -3]["value"]
                return next(c["value"] for c in q["choices"] if c["description"] == "Desayunos")
            if q["name"] == "subcategory":
                assert data["selected_category"] == "Desayunos"
                assert all(c["value"] != "alien" for c in q["choices"])
                return next(c["value"] for c in q["choices"] if c["description"] == "Cacao")
            if q["name"] == "brand":
                return next(c["value"] for c in q["choices"] if c["description"] == "MARCA 123")
            return q["choices"][-3]["value"]

        raw = response(questions, choose)
        self.on_response(raw)
        return raw, CatalogDecisionResponse.model_validate(raw)


class NoGenerator:
    effort = "none"

    def propose(self, *args):
        raise AssertionError("Generative call not allowed on covered or ambiguous cases")


def test_numeric_unknown_brand_survives_other_role_and_long_phrases():
    options, audit = prepare_options(source(), ENTRIES, ["ALMACEN"])
    assert "MARCA 123" in [o.label for o in options["brand"]]
    long_name = "MARCA CON NOMBRE MUY LARGO 180 G"
    opts, _ = prepare_options(source(long_name), [], [])
    assert "MARCA CON NOMBRE MUY LARGO" in [o.label for o in opts["brand"]]
    span = next(s for s in audit["spans"] if s["text"] == "MARCA 123")
    assert source()["original_name"][span["start"] : span["end"]] == span["text"]


def test_candidate_omissions_are_audited_and_category_not_lexically_filtered():
    opts, audit = prepare_options(source(), ENTRIES, [], span_budget=1)
    assert audit["omitted_spans"]
    assert opts["category"][0].label == "Desayunos"
    assert "unknown" in [c["value"] for c in questions_for(opts)[0]["choices"]]


def test_multiple_sizes_remain_candidates():
    opts, _ = prepare_options(source("PACK 6 X 180 G TOTAL 1080 G"), [], [])
    assert [o.data["value"] for o in opts["content"]] == ["180", "1080"]


def test_unknown_brand_and_hierarchy_work_without_generative(tmp_path):
    chooser = Chooser()
    pilot = Pilot(chooser, NoGenerator(), CallJournal(tmp_path / "calls.json", 0.1))
    row = pilot.interpret(
        source(), [*ENTRIES, entry("alien", "subcategory", "Otro", "other")], ["ALMACEN"]
    )
    assert row["brand"] == "MARCA 123"
    assert row["category"] == "Desayunos"
    assert row["subcategory"] == "Cacao"
    assert row["product_type"] == "Cacao"
    assert chooser.calls == 2
    # A simulated failure persisting row must not spend again when interpretation resumes.
    resumed = Pilot(chooser, NoGenerator(), CallJournal(tmp_path / "calls.json", 0.1))
    assert resumed.interpret(source(), ENTRIES, ["ALMACEN"])["brand"] == "MARCA 123"
    assert chooser.calls == 2


def test_unknown_category_never_calls_generator(tmp_path):
    pilot = Pilot(
        Chooser(category="unknown"), NoGenerator(), CallJournal(tmp_path / "calls.json", 0.1)
    )
    assert pilot.interpret(source(), ENTRIES, ["ALMACEN"])["category"] is None


def test_gap_is_auxiliary_only_and_new_node_is_not_auto_accepted(tmp_path):
    class Generator:
        effort = "none"

        def propose(self, src, attribute, entries, parent_id):
            assert attribute == "category"
            assert parent_id is None
            raw = {
                "usage": {"input_tokens": 40, "output_tokens": 25},
                "output": [
                    {
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"action":"create","existing_id":null,"label":"Bebidas",'
                                '"parent_id":null,"aliases":[],"evidence":"CACAO"}',
                            }
                        ]
                    }
                ],
            }
            self.on_response(raw)
            return raw, None

    pilot = Pilot(Chooser(category="none"), Generator(), CallJournal(tmp_path / "calls.json", 0.1))
    row = pilot.interpret(source(), ENTRIES, [])
    assert row["category"] is None
    assert row["attributes"]["category"]["status"] == "proposed_new"
    assert row["attributes"]["category"]["proposed_value"] == "Bebidas"
    assert row["attributes"]["subcategory"]["value"] is None
    # Reaching the proposal cap must not prevent recovering an already paid proposal.
    pilot.max_proposals = 1
    recovered = pilot.gap(source(), "category", ENTRIES, None)
    assert recovered["proposed_value"] == "Bebidas"


def test_budget_stops_before_call_and_unknown_consumption_cannot_repeat(tmp_path):
    journal = CallJournal(tmp_path / "calls.json", 0.0000001)
    invoked = []
    with pytest.raises(BudgetStop):
        journal.call("classification", {}, lambda cb: invoked.append(True), lambda x: x)
    assert not invoked
    journal = CallJournal(tmp_path / "calls.json", 0.1)

    def failed(callback):
        invoked.append(True)
        raise TimeoutError()

    with pytest.raises(TimeoutError):
        journal.call("classification", {}, failed, lambda x: x)
    with pytest.raises(BudgetStop):
        CallJournal(tmp_path / "calls.json", 0.1).call("classification", {}, failed, lambda x: x)
    assert len(invoked) == 1


def test_raw_invalid_response_keeps_billed_usage_before_parse(tmp_path):
    journal = CallJournal(tmp_path / "calls.json", 0.1)
    raw = dict(usage=dict(input_tokens=100), answers=[])
    with pytest.raises(ValueError):
        journal.call(
            "classification",
            {},
            lambda callback: callback(raw),
            lambda value: (_ for _ in ()).throw(ValueError()),
        )
    assert journal.cost > 0
    assert next(iter(journal.records.values()))["response"] == raw


def test_decisions_cost_does_not_charge_cache_or_output(tmp_path):
    journal = CallJournal(tmp_path / "calls.json", 0.1)
    raw = dict(
        usage=dict(
            input_tokens=100,
            output_tokens=1000,
            input_tokens_details=dict(cached_tokens=70, cache_write_tokens=30),
        )
    )
    journal.call("classification", {}, lambda callback: callback(raw), lambda x: x)
    assert float(journal.cost) == pytest.approx(0.00001)


def test_dry_run_has_no_provider_calls_and_tenant_is_checked(tmp_path):
    snapshot = dict(business_id="a", sources=[source()], vocabulary=ENTRIES, families=[])
    report = run(snapshot, tmp_path, chooser=SimpleNamespace(), proposer=SimpleNamespace())
    assert report["state"] == "dry_run"
    assert not (tmp_path / "calls.json").exists()
    snapshot["sources"][0]["business_id"] = "b"
    with pytest.raises(ValueError):
        run(snapshot, tmp_path)


def test_family_decision_is_a_suggestion_only():
    state = dict(status="interpreted", value="BEBIDAS", probability=0.99)
    notice = family_result(source(), state)
    assert notice["status"] == "change_suggested"
    assert notice["requires_user_review"]
    assert not notice["original_family_modified"]


def test_all_categories_survive_255_choice_limit_without_lexical_filtering():
    entries = [entry(f"c{i}", "category", f"Categoría {i}") for i in range(285)]
    opts, _ = prepare_options(source(), entries, [])
    groups = {k: v for k, v in opts.items() if k.startswith("category_group_")}
    assert sum(len(v) for v in groups.values()) == 285
    assert all(len(q["choices"]) <= 255 for q in questions_for(opts))


def test_compact_wire_ids_are_remapped_before_validation(tmp_path):
    class CompactChooser:
        on_response = None

        def decide(self, data, questions):
            assert all(c["value"].startswith("o") for q in questions for c in q["choices"])
            raw = response(questions, lambda q: q["choices"][0]["value"])
            self.on_response(raw)
            return raw, CatalogDecisionResponse.model_validate(raw)

    pilot = Pilot(CompactChooser(), NoGenerator(), CallJournal(tmp_path / "calls.json", 0.1))
    opts = {"category": prepare_options(source(), ENTRIES, [])[0]["category"]}
    assert pilot.decide(source(), opts, "classification")["category"]["value"] == "Desayunos"


def test_export_rejects_cross_tenant_and_edited_snapshot(tmp_path):
    from copy import deepcopy

    from scripts.export_catalog_pilot_sql import guarded_sql

    row = Pilot(Chooser(), NoGenerator(), CallJournal(tmp_path / "calls.json", 0.1)).interpret(
        source(), ENTRIES, []
    )
    row["run_id"] = "run"
    sql = guarded_sql([row], "a")
    assert "FOR UPDATE OF p" in sql
    assert "r.source=jsonb_build_object" in sql
    assert "ON CONFLICT" in sql
    changed = deepcopy(row)
    changed["source"]["original_name"] = "edited"
    with pytest.raises(ValueError):
        guarded_sql([changed], "a")
    with pytest.raises(ValueError):
        guarded_sql([row], "b")
