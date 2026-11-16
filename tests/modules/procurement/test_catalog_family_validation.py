import pytest

from app.modules.procurement.catalog_family_validation import validate_family_answer
from app.providers.catalog_decisions import CatalogDecisionResponse


def response(choice, probabilities, confidence=1):
    return CatalogDecisionResponse.model_validate(
        {
            "model": "gpt-6-luna",
            "usage": {"input_tokens": 1},
            "answers": [
                {
                    "type": "choice",
                    "name": "original_family_membership",
                    "choice": choice,
                    "confidence": confidence,
                    "probabilities": [
                        {"value": key, "probability": value} for key, value in probabilities
                    ],
                }
            ],
        }
    )


@pytest.mark.parametrize("choice", ["compatible", "incompatible"])
def test_strong_membership_signal_can_generate_warning_or_compatibility(choice):
    values = [
        (key, 0.98 if key == choice else 0.01) for key in ("compatible", "incompatible", "unknown")
    ]
    assert validate_family_answer(response(choice, values))["status"] == choice


def test_reported_confidence_does_not_override_uncertain_distribution():
    values = [("compatible", 0.40), ("incompatible", 0.55), ("unknown", 0.05)]
    assert validate_family_answer(response("incompatible", values))["status"] == "needs_review"


def test_unknown_is_not_an_incorrect_original_family():
    values = [("compatible", 0.01), ("incompatible", 0.01), ("unknown", 0.98)]
    assert validate_family_answer(response("unknown", values))["status"] == "unknown"


@pytest.mark.parametrize(
    "values",
    [
        [("compatible", 0.99)],
        [("compatible", 0.98), ("incompatible", 0.01), ("incompatible", 0.01)],
        [("compatible", 0.8), ("incompatible", 0.01), ("unknown", 0.01)],
        [("compatible", 0.01), ("incompatible", 0.98), ("unknown", 0.01)],
    ],
)
def test_invalid_distribution_or_choice_mismatch_does_not_mark_family(values):
    assert validate_family_answer(response("compatible", values))["status"] == "needs_review"
