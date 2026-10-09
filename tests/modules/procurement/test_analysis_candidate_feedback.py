"""Offline analysis matching: no SQL repository, provider or network calls."""

from datetime import timedelta
from decimal import Decimal

import pytest

from app.modules.procurement.analysis_contracts import OfferInterpretation
from app.modules.procurement.identity import identity_conflicts
from app.modules.procurement.matching import ProductMatchService
from app.modules.procurement.normalization import normalize_text
from app.modules.procurement.schemas import (
    Product,
    ProductMatchCandidate,
    ProductMatchFeedbackRule,
    SupplierOfferItem,
    SupplierProductRelationship,
)


def offer(name="Arroz Gallo tradicional 1kg", **updates):
    return SupplierOfferItem(
        business_id="b",
        supplier_id="s",
        supplier_offer_document_id="d",
        raw_name=name,
        normalized_name=normalize_text(name),
        offer_price=Decimal("100"),
        **updates,
    )


def product(name="Arroz Gallo tradicional 1kg", **updates):
    return Product(
        business_id="b",
        name=name,
        normalized_name=normalize_text(name),
        current_cost=Decimal("200"),
        **updates,
    )


def feedback(item, selected, *, accepted=True, relationship="exact_match", **updates):
    return ProductMatchFeedbackRule(
        supplier_id=item.supplier_id,
        supplier_offer_item_normalized_name=item.normalized_name,
        candidate_product_id=selected.id,
        accepted=accepted,
        relationship_type=SupplierProductRelationship(relationship),
        created_at=updates.pop("created_at", item.created_at),
        **updates,
    )


class Repository:
    """Deliberately returns unfiltered rows to exercise service defenses."""

    def __init__(self, products, items=(), rules=()):
        self.products, self.items, self.rules = products, list(items), list(rules)
        self.catalog_calls = 0
        self.queries = []

    def list_products(self, *, business_id):
        self.catalog_calls += 1
        self.queries.append(business_id)
        return self.products

    def list_supplier_offer_items(self, *, business_id, supplier_offer_document_id):
        self.queries.append(business_id)
        return self.items

    def list_product_match_feedback_rules(self, *, business_id, supplier_id, normalized_names):
        self.queries.append(business_id)
        return self.rules


def retrieve(item, products, rules=()):
    repo = Repository(products, [item], rules)
    return ProductMatchService(repo).retrieve_identity_candidates(
        business_id="b",
        offer_items=[item],
    )


def test_vital_analysis_prioritizes_unique_canonical_candidate_before_fuzzy():
    original = product("LAVANDINA AYUDIN 2L ORIGINAL", id="original", unit_size=2, unit="l")
    lavanda = product("AYUDIN LAVANDINA LAVANDA 2L", id="lavanda", unit_size=2, unit="l")
    offered = OfferInterpretation(
        offer_id="offer",
        block_id="block",
        business_id="b",
        raw_name="AYUDIN Lavandina original x2lt.",
        unit_size=2,
        unit="l",
        offer_price=1869,
    )
    found = ProductMatchService(Repository([lavanda, original])).retrieve_analysis_candidates(
        business_id="b",
        supplier_id="s",
        offers=[offered],
    )["offer"]
    assert found[0].product_id == "original"
    assert "auto_accepted_identity_v1" in found[0].reason_codes
    assert "canonical_identity_v2" in found[0].reason_codes
    assert all(candidate.product_id != "lavanda" for candidate in found)
    duplicate = original.model_copy(update={"id": "duplicate"})
    found = ProductMatchService(Repository([original, duplicate])).retrieve_analysis_candidates(
        business_id="b",
        supplier_id="s",
        offers=[offered],
    )["offer"]
    assert not any("auto_accepted_identity_v1" in candidate.reason_codes for candidate in found)


def test_compatible_feedback_resolves_before_fuzzy_without_mutating_rule():
    item, selected = offer(), product()
    rule = feedback(item, selected)
    original = rule.model_dump()
    [candidate] = retrieve(item, [selected, product(id="duplicate")], [rule])
    assert candidate.product.id == selected.id
    assert candidate.auto_accepted
    assert candidate.recommendation == "buy"
    assert rule.model_dump() == original


@pytest.mark.parametrize(
    "name,updates,reason",
    [
        ("Arroz Gallo tradicional 500g", {}, "presentation_conflict"),
        ("Arroz Gallo suave 1kg", {}, "variant_conflict"),
        ("Arroz Gallo tradicional 1kg", {"brand": "Otra"}, "brand_conflict"),
        ("Arroz Gallo tradicional 1kg", {"active": False}, "inactive_product"),
    ],
)
def test_feedback_conflict_is_retained_without_substituting_fuzzy_winner(name, updates, reason):
    item = offer(brand="Gallo")
    selected = product(name, **updates)
    rule = feedback(item, selected)
    original = rule.model_dump()
    [candidate] = retrieve(item, [selected, product(id="fuzzy")], [rule])
    assert candidate.product.id == selected.id
    assert "confirmed_by_feedback" in candidate.reasons
    assert reason in candidate.reasons
    assert not candidate.auto_accepted
    assert candidate.recommendation == "review"
    assert candidate.cost_difference is None
    assert rule.model_dump() == original


def test_feedback_other_tenant_cannot_resolve_or_replace_with_auto_match():
    item = offer()
    foreign = product(id="foreign").model_copy(update={"business_id": "other"})
    [candidate] = retrieve(item, [foreign, product()], [feedback(item, foreign)])
    assert candidate.product is None
    assert "feedback_product_unavailable" in candidate.reasons
    assert not candidate.auto_accepted


def test_latest_rejection_wins_even_with_unsorted_feedback():
    item, selected = offer(), product()
    old = feedback(item, selected)
    latest = feedback(
        item,
        selected,
        accepted=False,
        created_at=item.created_at + timedelta(days=1),
    )
    [candidate] = retrieve(item, [selected], [old, latest])
    assert candidate.product is None


def test_multiple_accepted_rules_remain_pending_even_with_public_limit_one():
    item = offer()
    products = [product(id="a"), product(id="b")]
    repo = Repository(products, [item], [feedback(item, p) for p in products])
    [candidate] = (
        ProductMatchService(repo)
        .compare_supplier_offer(
            business_id="b",
            supplier_offer_document_id="d",
        )
        .candidates
    )
    assert "multiple_accepted_feedback" in candidate.reasons
    assert not candidate.auto_accepted


def test_comparable_feedback_is_not_confirmed_identity():
    item, selected = offer(), product()
    [candidate] = retrieve(
        item,
        [selected],
        [feedback(item, selected, relationship="comparable_alternative")],
    )
    assert candidate.relationship_type == SupplierProductRelationship.COMPARABLE_ALTERNATIVE
    assert candidate.reasons == ["confirmed_by_feedback"]
    assert not candidate.auto_accepted
    assert candidate.recommendation == "review"
    assert candidate.cost_difference == Decimal("100.00")


def test_internal_five_public_one_and_catalog_loaded_once_for_all_offers():
    items = [offer(), offer("Arroz Gallo tradicional 1kg", id="second")]
    products = [product(id=f"p{i}") for i in range(7)]
    repo = Repository(products, items)
    service = ProductMatchService(repo)
    candidates = service.retrieve_identity_candidates(business_id="b", offer_items=items)
    assert len(candidates) == 10
    assert repo.catalog_calls == 1
    assert set(repo.queries) == {"b"}
    assert all(not c.auto_accepted and c.recommendation == "review" for c in candidates)
    report = service.compare_supplier_offer(business_id="b", supplier_offer_document_id="d")
    assert len(report.candidates) == 2


def test_unmatched_offer_preserved_and_foreign_products_excluded():
    item = offer("Producto inexistente")
    foreign = product().model_copy(update={"business_id": "foreign"})
    [candidate] = retrieve(item, [foreign])
    assert candidate.product is None
    assert candidate.supplier_offer_item.id == item.id


def test_identity_pending_never_recommends_buy_or_do_not_buy():
    item = offer()
    products = [product(id="a"), product(id="b")]
    for price in [Decimal("100"), Decimal("300")]:
        item.offer_price = price
        [candidate] = ProductMatchService(None).match_item(item=item, products=products)
        assert not candidate.auto_accepted
        assert candidate.recommendation == "review"
        assert candidate.cost_difference is None
        assert candidate.estimated_margin_percentage is None


def test_price_changes_do_not_change_candidate_identity_order():
    item = offer()
    products = [product(id="a"), product("Arroz Gallo 1kg", id="b")]
    first = retrieve(item, products)
    item.offer_price = Decimal("999999")
    products[0].current_cost = Decimal("0.01")
    second = retrieve(item, products)
    assert [(c.product.id, c.confidence_score, c.auto_accepted) for c in first] == [
        (c.product.id, c.confidence_score, c.auto_accepted) for c in second
    ]


def test_canonical_presentations_and_explicit_variant_metadata():
    item = offer(unit_size=Decimal("1"), unit="kg")
    selected = product(unit_size=Decimal("1000"), unit="g")
    assert identity_conflicts(item, selected) == []
    item.metadata["variant"] = "suave"
    assert "variant_conflict" in identity_conflicts(item, selected)


def test_foreign_offer_fails_before_repository_access():
    repo = Repository([])
    item = offer().model_copy(update={"business_id": "other"})
    with pytest.raises(ValueError, match="tenant"):
        ProductMatchService(repo).retrieve_identity_candidates(business_id="b", offer_items=[item])
    assert repo.catalog_calls == 0


def interpretation(item):
    return OfferInterpretation(
        offer_id=item.id,
        block_id="block",
        business_id=item.business_id,
        raw_name=item.raw_name,
        offer_price=None,
        brand=item.brand,
        unit_size=item.unit_size,
        unit=item.unit,
    )


def test_v2_missing_price_matches_with_shared_dtos_and_one_catalog_query():
    items = [offer(), offer("No existe", id="unmatched")]
    offers = [interpretation(item) for item in items]
    repo = Repository([product()])
    result = ProductMatchService(repo).retrieve_analysis_candidates(
        business_id="b",
        supplier_id="s",
        offers=offers,
    )
    assert repo.catalog_calls == 1
    assert result[items[0].id][0].name == items[0].raw_name
    assert "auto_accepted_identity_v1" in result[items[0].id][0].reason_codes
    assert result["unmatched"] == []
    assert all(o.offer_price is None for o in offers)


def test_v2_feedback_conflict_retained_with_audit_collector():
    item = offer()
    wrong = product("Arroz Gallo suave 1kg")
    rule = feedback(item, wrong)
    repo = Repository([wrong, product()], rules=[rule])
    audit = {}
    result = ProductMatchService(repo).retrieve_analysis_candidates(
        business_id="b",
        supplier_id="s",
        offers=[interpretation(item)],
        feedback_conflicts=audit,
    )
    [candidate] = result[item.id]
    assert candidate.product_id == wrong.id
    assert "variant_conflict" in candidate.reason_codes
    assert audit[item.id] == ["variant_conflict"]
    assert rule.accepted


def test_v2_missing_feedback_target_leaves_audit_reason_and_no_fuzzy_substitute():
    item = offer()
    missing = product(id="deleted")
    repo = Repository([product()], rules=[feedback(item, missing)])
    audit = {}
    result = ProductMatchService(repo).retrieve_analysis_candidates(
        business_id="b",
        supplier_id="s",
        offers=[interpretation(item)],
        feedback_conflicts=audit,
    )
    assert result[item.id] == []
    assert audit[item.id] == ["feedback_product_unavailable"]


def test_v2_top_five_considers_sixth_runner_and_respects_latest_rejection():
    item = offer()
    products = [product(id=f"p{i}") for i in range(6)]
    repo = Repository(products, rules=[feedback(item, products[0], accepted=False)])
    result = ProductMatchService(repo).retrieve_analysis_candidates(
        business_id="b",
        supplier_id="s",
        offers=[interpretation(item)],
    )
    assert len(result[item.id]) == 5
    assert "p0" not in {c.product_id for c in result[item.id]}
    assert all("auto_accepted_identity_v1" not in c.reason_codes for c in result[item.id])


def test_explicit_human_correction_recalculates_even_with_prior_low_fuzzy_score():
    item, selected = offer(), product()
    candidate = ProductMatchCandidate(
        supplier_offer_item=item,
        product=selected,
        confidence_score=Decimal("0.45"),
        relationship_type=SupplierProductRelationship.SAME_PRODUCT_DIFFERENT_NAME,
        reasons=["corrected_by_user"],
    )
    corrected = ProductMatchService._with_commercials(candidate)
    assert corrected.auto_accepted
    assert corrected.cost_difference == Decimal("100.00")
    assert corrected.recommendation == "buy"
    selected.current_cost = Decimal("50")
    corrected = ProductMatchService._with_commercials(candidate)
    assert corrected.cost_difference == Decimal("-50.00")
    assert corrected.recommendation == "do_not_buy"


def test_correction_with_price_pending_confirms_identity_without_commercial_recommendation():
    item, selected = offer(price_type="unknown"), product()
    candidate = ProductMatchCandidate(
        supplier_offer_item=item,
        product=selected,
        confidence_score=Decimal("0.45"),
        relationship_type=SupplierProductRelationship.SAME_PRODUCT_DIFFERENT_NAME,
        reasons=["corrected_by_user"],
    )
    corrected = ProductMatchService._with_commercials(candidate)
    assert corrected.auto_accepted
    assert corrected.recommendation == "review"
    assert corrected.cost_difference is None


def test_conflicting_human_correction_cannot_reuse_old_savings_or_acceptance():
    item, selected = offer(), product("Arroz Gallo suave 1kg")
    candidate = ProductMatchCandidate(
        supplier_offer_item=item,
        product=selected,
        confidence_score=Decimal("1"),
        relationship_type=SupplierProductRelationship.SAME_PRODUCT_DIFFERENT_NAME,
        reasons=["corrected_by_user"],
        auto_accepted=True,
        cost_difference=Decimal("100"),
        recommendation="buy",
    )
    corrected = ProductMatchService._with_commercials(candidate)
    assert not corrected.auto_accepted
    assert corrected.cost_difference is None
    assert corrected.recommendation == "review"
    assert "variant_conflict" in corrected.reasons


def test_human_accepted_other_brand_alternative_keeps_comparison_but_size_veto():
    item = offer(brand="Gallo")
    selected = product("Arroz Otra tradicional 1kg", brand="Otra")
    [candidate] = retrieve(
        item,
        [selected],
        [feedback(item, selected, relationship="comparable_alternative")],
    )
    assert candidate.reasons == ["confirmed_by_feedback"]
    assert not candidate.auto_accepted
    assert candidate.cost_difference == Decimal("100.00")
    assert candidate.recommendation == "review"
    selected.unit_size, selected.unit = Decimal("500"), "g"
    [candidate] = retrieve(
        item,
        [selected],
        [feedback(item, selected, relationship="comparable_alternative")],
    )
    assert candidate.cost_difference is None
    assert "presentation_inconsistent" in candidate.reasons


@pytest.mark.parametrize("currency", ["USD", " usd ", "EUR", "$"])
def test_foreign_currency_preserves_identity_and_offer_without_ars_comparison(currency):
    item = offer(currency=currency)
    item.offer_price = Decimal("1")
    selected = product(sale_price=Decimal("100"))
    selected.current_cost = Decimal("100")
    [candidate] = ProductMatchService(None).match_item(item=item, products=[selected])
    assert candidate.auto_accepted
    assert candidate.supplier_offer_item.offer_price == Decimal("1")
    assert candidate.supplier_offer_item.currency == currency
    assert candidate.recommendation == "review"
    assert "currency_not_comparable" in candidate.reasons
    assert candidate.cost_difference is None
    assert candidate.cost_difference_percentage is None
    assert candidate.estimated_margin_percentage is None


def test_currency_gate_clears_prior_calculations_and_precedes_pack_heuristic(monkeypatch):
    item = offer()
    selected = product(sale_price=Decimal("300"))
    [candidate] = ProductMatchService(None).match_item(item=item, products=[selected])
    assert candidate.recommendation == "buy"
    assert candidate.cost_difference == Decimal("100.00")
    assert candidate.estimated_margin_percentage == Decimal("200.00")
    item.currency = "USD"
    item.package_quantity = 6

    def unexpected_heuristic(**kwargs):
        raise AssertionError("Foreign currency must not enter the ARS pack heuristic")

    monkeypatch.setattr(
        "app.modules.procurement.matching.is_likely_pack_price",
        unexpected_heuristic,
    )
    candidate = ProductMatchService._with_commercials(candidate)
    assert candidate.auto_accepted
    assert candidate.recommendation == "review"
    assert candidate.cost_difference is None
    assert candidate.cost_difference_percentage is None
    assert candidate.estimated_margin_percentage is None


def test_normalized_ars_keeps_existing_commercial_comparison():
    item = offer(currency=" ars ")
    selected = product(sale_price=Decimal("300"))
    [candidate] = ProductMatchService(None).match_item(item=item, products=[selected])
    assert candidate.auto_accepted
    assert candidate.recommendation == "buy"
    assert candidate.cost_difference == Decimal("100.00")
    assert candidate.cost_difference_percentage == Decimal("50.00")
    assert candidate.estimated_margin_percentage == Decimal("200.00")


def v2_metadata(item, selected, *, identity_status="confirmed", price_status="confirmed"):
    return {
        "analysis_v2": {
            "interpretation": {
                **interpretation(item).model_dump(mode="json"),
                "offer_price": str(item.offer_price),
                "price_type": item.price_type.value,
                "identity_status": identity_status,
                "price_status": price_status,
                "product_id": selected.id,
            }
        }
    }


@pytest.mark.parametrize("status", ["pending", "no_match"])
def test_v2_pending_identity_stays_pending_on_two_service_comparisons(status):
    item, selected = offer(), product(sale_price=Decimal("300"))
    item.metadata = v2_metadata(item, selected, identity_status=status)
    repo = Repository([selected], [item])
    service = ProductMatchService(repo)
    for cost in [Decimal("200"), Decimal("400")]:
        selected.current_cost = cost
        [candidate] = service.compare_supplier_offer(
            business_id="b",
            supplier_offer_document_id="d",
        ).candidates
        assert not candidate.auto_accepted
        assert candidate.recommendation == "review"
        assert "analysis_policy_review_required" in candidate.reasons
        assert candidate.cost_difference is None
        assert candidate.cost_difference_percentage is None
        assert candidate.estimated_margin_percentage is None


def test_v2_confirmed_identity_pending_price_preserves_identity_without_comparison():
    item, selected = offer(), product(sale_price=Decimal("300"))
    # Even a legacy unit default cannot upgrade the persisted pending price.
    item.metadata = v2_metadata(item, selected, price_status="pending")
    service = ProductMatchService(Repository([selected], [item]))
    for _ in range(2):
        [candidate] = service.compare_supplier_offer(
            business_id="b",
            supplier_offer_document_id="d",
        ).candidates
        assert candidate.auto_accepted
        assert candidate.recommendation == "review"
        assert candidate.cost_difference is None
        assert candidate.estimated_margin_percentage is None
        assert "analysis_policy_review_required" in candidate.reasons


def test_v2_other_confirmed_product_cannot_be_replaced_by_new_fuzzy_winner():
    item, selected = offer(), product()
    old_selected = product(id="prior-policy-product")
    item.metadata = v2_metadata(item, old_selected)
    service = ProductMatchService(Repository([selected], [item]))
    for _ in range(2):
        [candidate] = service.compare_supplier_offer(
            business_id="b",
            supplier_offer_document_id="d",
        ).candidates
        assert candidate.product.id == selected.id
        assert not candidate.auto_accepted
        assert candidate.recommendation == "review"
        assert candidate.cost_difference is None


def test_v2_compatible_human_feedback_overrides_identity_and_survives_recomparison():
    item, selected = offer(), product()
    item.metadata = v2_metadata(
        item,
        product(id="prior-policy-product"),
        identity_status="no_match",
    )
    rule = feedback(item, selected)
    service = ProductMatchService(Repository([selected], [item], [rule]))
    for _ in range(2):
        [candidate] = service.compare_supplier_offer(
            business_id="b",
            supplier_offer_document_id="d",
        ).candidates
        assert candidate.auto_accepted
        assert candidate.recommendation == "buy"
        assert candidate.cost_difference == Decimal("100.00")
        assert rule.accepted


def test_v2_human_identity_correction_does_not_confirm_pending_price():
    item, selected = offer(), product()
    item.metadata = v2_metadata(item, selected, identity_status="pending", price_status="pending")
    candidate = ProductMatchCandidate(
        supplier_offer_item=item,
        product=selected,
        confidence_score=Decimal("0.45"),
        relationship_type=SupplierProductRelationship.SAME_PRODUCT_DIFFERENT_NAME,
        reasons=["corrected_by_user"],
    )
    candidate = ProductMatchService._with_commercials(candidate)
    assert candidate.auto_accepted
    assert candidate.recommendation == "review"
    assert candidate.cost_difference is None


@pytest.mark.parametrize(
    "review",
    [
        {},
        {"status": "confirmed"},
        {"status": "confirmed", "decision": {"price_type": "unit", "confidence": 0.99}},
    ],
)
def test_v2_unvalidated_pack_review_cannot_confirm_pending_price(review):
    item, selected = offer(), product()
    item.metadata = v2_metadata(item, selected, price_status="pending")
    item.metadata["pack_review"] = review
    [candidate] = ProductMatchService(None).match_item(item=item, products=[selected])
    assert candidate.auto_accepted
    assert candidate.recommendation == "review"
    assert candidate.cost_difference is None


def test_v2_existing_valid_price_review_allows_comparison_without_identity_override():
    item, selected = offer(), product()
    item.metadata = v2_metadata(item, selected, price_status="pending")
    item.metadata["pack_review"] = {
        "status": "confirmed",
        "decision": {
            "price_type": "unit",
            "package_quantity": None,
            "retail_contents_count": None,
            "confidence": 0.99,
            "evidence": "Precio por unidad",
            "reason": "Verified review",
        },
    }
    [candidate] = ProductMatchService(None).match_item(item=item, products=[selected])
    assert candidate.auto_accepted
    assert candidate.recommendation == "buy"
    assert candidate.cost_difference == Decimal("100.00")
    # The review cannot validate an amount absent from the observed interpretation.
    item.metadata["analysis_v2"]["interpretation"]["offer_price"] = None
    [candidate] = ProductMatchService(None).match_item(item=item, products=[selected])
    assert candidate.recommendation == "review"
    assert candidate.cost_difference is None


@pytest.mark.parametrize("analysis", [None, {}, {"interpretation": "invalid"}])
def test_v2_malformed_metadata_fails_closed_without_affecting_legacy(analysis):
    item, selected = offer(), product()
    item.metadata["analysis_v2"] = analysis
    [candidate] = ProductMatchService(None).match_item(item=item, products=[selected])
    assert not candidate.auto_accepted
    assert candidate.recommendation == "review"
    item.metadata.clear()
    [candidate] = ProductMatchService(None).match_item(item=item, products=[selected])
    assert candidate.auto_accepted
    assert candidate.recommendation == "buy"


def test_v2_human_feedback_cannot_override_presentation_or_currency_checks():
    item, selected = offer(), product("Arroz Gallo tradicional 500g")
    item.metadata = v2_metadata(item, selected, identity_status="pending")
    rule = feedback(item, selected)
    [candidate] = retrieve(item, [selected], [rule])
    assert not candidate.auto_accepted
    assert candidate.recommendation == "review"
    selected = product()
    item.metadata = v2_metadata(item, selected, identity_status="pending")
    item.currency = "USD"
    [candidate] = retrieve(item, [selected], [feedback(item, selected)])
    assert candidate.auto_accepted
    assert candidate.recommendation == "review"
    assert "currency_not_comparable" in candidate.reasons
    assert candidate.cost_difference is None


def test_v2_human_alternative_does_not_reuse_old_exact_identity_confirmation():
    item, selected = offer(), product()
    item.metadata = v2_metadata(item, selected)
    [candidate] = retrieve(
        item,
        [selected],
        [
            feedback(
                item,
                selected,
                relationship="comparable_alternative",
            )
        ],
    )
    assert not candidate.auto_accepted
    assert candidate.recommendation == "review"
    assert candidate.cost_difference is None
