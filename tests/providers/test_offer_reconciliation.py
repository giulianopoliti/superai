from decimal import Decimal

import pytest

from app.modules.procurement.schemas import ExtractedSupplierOfferItem as Item
from app.providers.documents.reconciliation import OfferReconciliationDecision, reconcile_items


def offer(name="Mayonesa Natura Sachet 125 gr", price="100", **kwargs):
    return Item(
        raw_name=name, offer_price=price, page_number=kwargs.pop("page_number", 1), **kwargs
    )


class JevSpy:
    def __init__(self):
        self.calls = []

    def reconcile(self, *, observations):
        self.calls.append(observations)
        return OfferReconciliationDecision(
            decision="same_offer",
            confidence="0.87",
            selected_source="gemini",
            compatible_fields=["brand"],
            conflicting_fields=[],
            reason="Misma oferta.",
        )


def test_equivalent_names_select_complete_observation_and_preserve_originals():
    a = offer()
    b = offer("NATURA MAYONESA X125G", brand="NATURA", unit_size=125, unit="g", package_quantity=12)
    before = [a.model_dump(), b.model_dump()]
    items, unresolved = reconcile_items({"gemini": [a], "openai": [b]})
    assert not unresolved
    assert len(items) == 1
    item = items[0]
    assert item.raw_name == "Natura Mayonesa 125 g"
    assert item.agreement_status == "agreed_fuzzy"
    assert item.agreement_score == Decimal("0.96")
    assert item.selected_source == "openai"
    assert item.provider_observations["gemini"]["raw_name"] == a.raw_name
    assert item.provider_observations["openai"]["raw_name"] == b.raw_name
    assert [a.model_dump(), b.model_dump()] == before


@pytest.mark.parametrize(
    ("right", "status"),
    [
        (offer(price="120"), "price_conflict"),
        (offer("Mayonesa Natura Sachet 250 gr"), "presentation_conflict"),
        (offer(package_quantity=24), "presentation_conflict"),
    ],
)
def test_price_or_presentation_conflicts_are_retained_for_review(right, status):
    jev = JevSpy()
    items, unresolved = reconcile_items(
        {"gemini": [offer(package_quantity=12)], "openai": [right]}, jev
    )
    assert items == []
    assert unresolved[0]["agreement_status"] == status
    assert set(unresolved[0]["provider_observations"]) == {"gemini", "openai"}
    assert not jev.calls


def test_jev_receives_only_ambiguous_pairs_and_cannot_change_them():
    jev = JevSpy()
    readings = {
        "gemini": [offer("Mayonesa Natura light 125 g"), offer("Arroz 1kg"), offer("Agua", "20")],
        "openai": [offer("Mayonesa Natura 125 g"), offer("Arroz 1000g"), offer("Agua", "30")],
    }
    baseline_items, baseline_unresolved = reconcile_items(readings)
    items, unresolved = reconcile_items(readings, jev)
    assert len(jev.calls) == 1
    assert [item.model_dump() for item in items] == [item.model_dump() for item in baseline_items]
    assert len(unresolved) == len(baseline_unresolved)
    shadow = next(row for row in unresolved if "jev_shadow" in row)
    assert shadow["agreement_status"] == "ambiguous"
    assert shadow["selected_source"] is None
    assert shadow["resolved_by"] == "human_review"
    assert shadow["jev_shadow"]["decision"] == "same_offer"
    assert shadow["jev_shadow_status"] == "jev_shadow_agreement"


def test_different_pages_or_different_products_never_merge():
    items, unresolved = reconcile_items(
        {
            "gemini": [offer(page_number=1)],
            "openai": [offer(page_number=2), offer("Lentejas 1kg")],
        }
    )
    assert len(items) == 3
    assert not unresolved


def test_compatible_units_merge_and_missing_fields_are_completed():
    items, unresolved = reconcile_items(
        {
            "gemini": [offer("Arroz 1kg", brand="Gallo")],
            "openai": [offer("Arroz 1000 gr", package_quantity=12)],
        }
    )
    assert not unresolved
    assert len(items) == 1
    assert (items[0].unit_size, items[0].unit, items[0].package_quantity) == (1000, "g", 12)
    assert items[0].brand == "Gallo"


def test_reconciliation_does_not_arbitrarily_pair_duplicate_candidates():
    items, unresolved = reconcile_items(
        {
            "gemini": [offer(), offer()],
            "openai": [offer()],
        }
    )
    assert not any(item.agreement_status.startswith("agreed_") for item in items)
    assert unresolved[0]["agreement_status"] == "ambiguous"


def test_jev_invalid_response_is_logged_without_losing_valid_offers():
    class BadJev:
        def reconcile(self, **kwargs):
            return {"decision": "invent_product", "confidence": 2}

    items, unresolved = reconcile_items(
        {
            "gemini": [offer("Mayonesa Natura light 125 g"), offer("Arroz")],
            "openai": [offer("Mayonesa Natura 125 g"), offer("Arroz")],
        },
        BadJev(),
    )
    assert len(items) == 1
    assert unresolved[0]["jev_shadow_error"] == "ValidationError"


def test_normalization_preserves_secondary_sizes_in_offer_description():
    from app.providers.documents.reconciliation import normalize_observation

    item = offer("Arroz 1 kg + 100 g adicional")
    normalized = normalize_observation(item)
    assert normalized.raw_name == "Arroz 1000 g + 100 g Adicional"
    assert item.raw_name == "Arroz 1 kg + 100 g adicional"
