import pytest

from app.modules.procurement.catalog_taxonomy import (
    resolve_taxonomy,
    validate_proposal,
    with_reviewed_matches,
)
from app.providers.catalog_taxonomy_proposal import TaxonomyProposal
from tests.modules.procurement.test_catalog_interpretation import response_for


def entry(
    identifier, label, attribute="subcategory", parent_id="cookies", status="active", aliases=None
):
    return {
        "id": identifier,
        "label": label,
        "attribute": attribute,
        "parent_id": parent_id,
        "status": status,
        "aliases": aliases or [],
    }


def proposal(**kwargs):
    return TaxonomyProposal(
        action="create",
        existing_id=None,
        label="Galletitas de arroz",
        parent_id="cookies",
        aliases=["Galletas de arroz"],
        evidence="GALLETITAS ARROZ",
        **kwargs,
    )


def test_create_alias_reuses_existing_and_pending_entries():
    source = {"original_name": "DOS HERMANOS GALLETITAS ARROZ DULCES"}
    existing = entry("rice", "Galletitas de arroz", aliases=["Galletas de arroz"])
    result = validate_proposal(
        proposal(), source=source, attribute="subcategory", entries=[existing], parent_id="cookies"
    )
    assert result["status"] == "interpreted" and result["vocabulary_id"] == "rice"
    existing["status"] = "proposed"
    result = validate_proposal(
        proposal(), source=source, attribute="subcategory", entries=[existing], parent_id="cookies"
    )
    assert result["status"] == "proposed_new" and result["value"] is None


@pytest.mark.parametrize(
    "updates,reason",
    [
        ({"parent_id": "other"}, "parent_mismatch"),
        ({"evidence": "inventado"}, "unsupported_evidence"),
        ({"action": "reuse", "existing_id": "foreign"}, "id_outside_scope"),
    ],
)
def test_invalid_parent_evidence_and_ids_are_rejected(updates, reason):
    p = proposal().model_copy(update=updates)
    result = validate_proposal(
        p,
        source={"original_name": "GALLETITAS ARROZ"},
        attribute="subcategory",
        entries=[],
        parent_id="cookies",
    )
    assert result["status"] == "invalid_proposal" and result["reason"] == reason


class FakeChooser:
    def __init__(self, selections):
        self.selections = iter(selections)
        self.questions = []

    def decide(self, source, questions):
        assert "category" not in source  # Wrong imported family cannot bias this decision.
        from app.modules.procurement.catalog_interpretation import Option

        self.questions.append(questions)
        options = {
            q["name"]: [
                Option(c["value"], c["description"], "evidence", "vocabulary")
                for c in q["choices"]
                if c["value"] not in {"unknown", "none", "not_applicable"}
            ]
            for q in questions
        }
        selected = next(self.selections)
        result = response_for(options, {questions[0]["name"]: selected})
        return result.model_dump(mode="json"), result


class FakeProposer:
    def __init__(self):
        self.calls = 0

    def propose(self, source, attribute, entries, parent_id):
        self.calls += 1
        return {"usage": {"input_tokens": 10, "output_tokens": 10}}, proposal()


def test_none_expands_before_generation_and_unknown_never_generates():
    entries = [entry(str(i), f"Galletitas tipo {i}") for i in range(12)]
    entries.append(entry("cookies", "Galletitas", "category", None))
    source = {"original_name": "GALLETITAS ARROZ", "category": "ALIMENTO PARA ANIMALES"}
    chooser, proposer = FakeChooser(["none", "none"]), FakeProposer()
    state, stages = resolve_taxonomy(
        source,
        "subcategory",
        entries,
        parent_id="cookies",
        chooser=chooser,
        proposer=proposer,
        threshold=0.9,
    )
    assert [len(q[0]["choices"]) for q in chooser.questions] == [11, 15]
    assert proposer.calls == 1 and state["status"] == "proposed_new"
    assert [s["provider"] for s in stages] == ["decisions", "decisions", "responses"]
    chooser, proposer = FakeChooser(["unknown"]), FakeProposer()
    state, _ = resolve_taxonomy(
        {"original_name": "ASD", "category": "ALIMENTO PARA ANIMALES"},
        "subcategory",
        entries,
        parent_id="cookies",
        chooser=chooser,
        proposer=proposer,
        threshold=0.9,
    )
    assert state["status"] == "unknown" and proposer.calls == 0


def test_existing_option_does_not_call_generator():
    entries = [
        entry("rice", "Galletitas de arroz"),
        entry("cookies", "Galletitas", "category", None),
    ]
    proposer = FakeProposer()
    state, _ = resolve_taxonomy(
        {"original_name": "GALLETITAS ARROZ"},
        "subcategory",
        entries,
        parent_id="cookies",
        chooser=FakeChooser(["rice"]),
        proposer=proposer,
        threshold=0.9,
    )
    assert state["value"] == "Galletitas de arroz" and proposer.calls == 0


def test_parent_must_belong_to_same_business():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.db.models import Base, BusinessModel
    from app.db.repositories.catalog_interpretations import ensure_vocabulary

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([BusinessModel(id="a", name="A"), BusinessModel(id="b", name="B")])
        session.flush()
        parent = ensure_vocabulary(session, "a", "category", "Galletitas", [], status="active")
        with pytest.raises(ValueError, match="parent"):
            ensure_vocabulary(
                session,
                "b",
                "subcategory",
                "Galletitas de arroz",
                [],
                status="proposed",
                parent_id=parent.id,
            )


def test_failed_generator_preserves_pending_without_using_another_model():
    class BrokenProposer:
        def propose(self, *args):
            raise RuntimeError("Unavailable")

    state, stages = resolve_taxonomy(
        {"original_name": "GALLETITAS ARROZ"},
        "subcategory",
        [entry("cookies", "Galletitas", "category", None)],
        parent_id="cookies",
        chooser=FakeChooser(["none"]),
        proposer=BrokenProposer(),
        threshold=0.9,
    )
    assert state["status"] == "invalid_proposal" and state["value"] is None
    assert stages[-1]["error_type"] == "RuntimeError"


@pytest.mark.parametrize("variant", ["DULCES", "SALADAS", "SIN SAL"])
def test_pending_subcategory_can_be_selected_without_promotion(variant):
    entries = [
        entry("rice", "Galletitas de arroz", status="proposed"),
        entry("cookies", "Galletitas", "category", None),
    ]
    proposer = FakeProposer()
    state, _ = resolve_taxonomy(
        {"original_name": f"DOS HERMANOS GALLETITAS ARROZ {variant}"},
        "subcategory",
        entries,
        parent_id="cookies",
        chooser=FakeChooser(["rice"]),
        proposer=proposer,
        threshold=0.9,
    )
    assert state["status"] == "proposed_new" and state["value"] is None
    assert state["vocabulary_id"] == "rice" and state["parent_id"] == "cookies"
    assert entries[0]["status"] == "proposed" and proposer.calls == 0


def test_recognizable_unknown_is_reviewed_but_cannot_create_new_concept():
    entries = [
        entry("rice", "Galletitas de arroz"),
        entry("cookies", "Galletitas", "category", None),
    ]

    class ReuseProposer:
        def propose(self, source, attribute, entries, parent_id):
            assert source["resolution_mode"] == "reuse_only"
            return {"usage": {"input_tokens": 1}}, proposal().model_copy(
                update={"action": "reuse", "existing_id": "rice"}
            )

    state, _ = resolve_taxonomy(
        {"original_name": "GALLETITAS ARROZ"},
        "subcategory",
        entries,
        parent_id="cookies",
        chooser=FakeChooser(["unknown"]),
        proposer=ReuseProposer(),
        threshold=0.9,
    )
    assert state["value"] == "Galletitas de arroz"
    state, _ = resolve_taxonomy(
        {"original_name": "GALLETITAS ARROZ"},
        "subcategory",
        entries,
        parent_id="cookies",
        chooser=FakeChooser(["unknown"]),
        proposer=FakeProposer(),
        threshold=0.9,
    )
    assert state["status"] == "unknown" and state["value"] is None
    assert state["reason"] == "uncertainty_does_not_allow_creation"


def test_reviewed_dolca_mapping_is_specific_and_independent_of_variants():
    entries = [
        entry("instant", "Café instantáneo", parent_id="coffee"),
        entry("coffee", "Café", "category", None),
    ]
    seed = [
        {
            "label": "Café",
            "children": [
                {"label": "Café instantáneo", "reviewed_matches": ["NESCAFE DOLCA ORIGEN"]}
            ],
        }
    ]
    mapped = with_reviewed_matches(entries, seed)
    for name in ("NESCAFE DOLCA ORIGEN 170GRS", "NESCAFE DOLCA ORIGEN SUAVE 170GRS"):
        state, stages = resolve_taxonomy(
            {"original_name": name},
            "subcategory",
            mapped,
            parent_id="coffee",
            chooser=FakeChooser([]),
            proposer=FakeProposer(),
            threshold=0.9,
        )
        assert state["value"] == "Café instantáneo" and state["origin"] == "reviewed_mapping"
        assert stages == []
    state, _ = resolve_taxonomy(
        {"original_name": "NESCAFE CAFE MOLIDO 170GRS"},
        "subcategory",
        mapped,
        parent_id="coffee",
        chooser=FakeChooser(["unknown"]),
        proposer=FakeProposer(),
        threshold=0.9,
    )
    assert state.get("origin") != "reviewed_mapping"
    mapped.append(entry("ground", "Café molido", parent_id="coffee", aliases=["CAFE MOLIDO"]))
    state, _ = resolve_taxonomy(
        {"original_name": "NESCAFE DOLCA ORIGEN CAFE MOLIDO 170GRS"},
        "subcategory",
        mapped,
        parent_id="coffee",
        chooser=FakeChooser([]),
        proposer=FakeProposer(),
        threshold=0.9,
    )
    assert state["status"] == "conflict" and state["value"] is None
