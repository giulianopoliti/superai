import pytest
from pydantic import ValidationError

from app.modules.procurement.analysis_contracts import (
    ChoiceDecision,
    EvidenceFragment,
    OfferTextBlock,
    ReaderBlocksExtractionV2,
    ReaderOfferProposalV2,
    ReaderStructuredExtractionV2,
)
from app.modules.procurement.schemas import ExtractedSupplierOfferItem


@pytest.mark.parametrize("model", [ReaderBlocksExtractionV2, ReaderStructuredExtractionV2])
def test_reader_wire_closed_required_nullable(model):
    schema = model.model_json_schema()
    for definition in [schema, *schema.get("$defs", {}).values()]:
        if definition.get("type") == "object":
            assert definition["additionalProperties"] is False
            assert set(definition["required"]) == set(definition["properties"])
    assert model(supplier_name=None, pages=[], warnings=[]).supplier_name is None


def test_v2_proposal_without_price_does_not_relax_legacy_price():
    fields = {name: None for name in ReaderOfferProposalV2.model_fields}
    fields.update(price_type="unknown", field_evidence=[], warnings=[])
    assert ReaderOfferProposalV2(**fields).offer_price is None
    with pytest.raises(ValidationError):
        ExtractedSupplierOfferItem(raw_name="Promo", offer_price=None)


def test_b_rejects_structured_proposals():
    with pytest.raises(ValidationError):
        ReaderBlocksExtractionV2(
            supplier_name=None,
            warnings=[],
            pages=[
                {
                    "page_number": 1,
                    "page_conditions": [],
                    "coverage": {
                        "estimated_offer_blocks": None,
                        "needs_visual_review": False,
                        "warnings": [],
                    },
                    "blocks": [
                        {
                            "fragments": [],
                            "page_condition_indices": [],
                            "warnings": [],
                            "proposals": [],
                        }
                    ],
                }
            ],
        )


def test_canonical_text_and_condition_membership():
    fragment = EvidenceFragment(
        fragment_id="f", text="  Aceite $1.200 ", order=0, legibility="readable"
    )
    data = dict(
        block_id="b",
        run_id="r",
        reading_id="read",
        page_number=1,
        order_on_page=0,
        full_text=fragment.text,
        fragments=[fragment],
    )
    assert OfferTextBlock(**data).full_text == fragment.text
    with pytest.raises(ValidationError):
        OfferTextBlock(**{**data, "full_text": fragment.text.strip()})
    with pytest.raises(ValidationError):
        OfferTextBlock(**data, condition_refs=["another-page"])


@pytest.mark.parametrize(
    "probabilities",
    [
        {"unit": float("nan")},
        {"unit": 1.1},
        {"unit": 0.4},
        {"pack": 1.0},
    ],
)
def test_invalid_choice_distribution(probabilities):
    with pytest.raises(ValidationError):
        ChoiceDecision(
            question_id="q",
            choice="unit",
            confidence=0.5,
            probabilities=probabilities,
            model="fixed",
            prompt_version="v1",
        )


def test_confidence_is_independent_from_selected_probability():
    choice = ChoiceDecision(
        question_id="q",
        choice="unit",
        confidence=0.5,
        probabilities={"unit": 0.8, "unknown": 0.2},
        model="fixed",
        prompt_version="v1",
    )
    assert choice.confidence != choice.probabilities[choice.choice]
