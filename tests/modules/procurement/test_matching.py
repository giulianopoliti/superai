from decimal import Decimal

from app.modules.procurement.matching import ProductMatchService
from app.modules.procurement.pricing import SupplierPriceType
from app.modules.procurement.schemas import (
    Product,
    ProductMatchFeedbackRule,
    SupplierOfferItem,
    SupplierProductRelationship,
)


def test_matcher_finds_same_product_with_reordered_vital_name() -> None:
    service = ProductMatchService(repository=None)  # type: ignore[arg-type]
    item = SupplierOfferItem(
        business_id="business-1",
        supplier_offer_document_id="document-1",
        supplier_id="supplier-1",
        raw_name="CAÑUELAS Aceite girasol 900ml",
        normalized_name="canuelas aceite girasol 900ml",
        brand="CAÑUELAS",
        unit_size=Decimal("900"),
        unit="ml",
        offer_price=Decimal("2999"),
    )
    product = Product(
        business_id="business-1",
        name="ACEITE CAÑUELAS GIRASOL 900ML",
        normalized_name="aceite canuelas girasol 900ml",
        brand="CAÑUELAS",
        unit_size=Decimal("900"),
        unit="ml",
        current_cost=Decimal("3120"),
        sale_price=Decimal("4200"),
    )

    [candidate] = service.match_item(item=item, products=[product])

    assert candidate.product == product
    assert candidate.confidence_score >= Decimal("0.75")
    assert candidate.relationship_type in {
        SupplierProductRelationship.SAME_PRODUCT_DIFFERENT_NAME,
        SupplierProductRelationship.EXACT_MATCH,
    }
    assert candidate.cost_difference == Decimal("121.00")
    assert candidate.recommendation == "buy"


def test_matcher_keeps_different_brand_as_reviewable_alternative() -> None:
    service = ProductMatchService(repository=None)  # type: ignore[arg-type]
    item = SupplierOfferItem(
        business_id="business-1",
        supplier_offer_document_id="document-1",
        supplier_id="supplier-1",
        raw_name="QUESO CREMA CREMIGAL X290GRS",
        normalized_name="queso crema cremigal x290grs",
        brand="CREMIGAL",
        unit_size=Decimal("290"),
        unit="g",
        offer_price=Decimal("2650"),
    )
    product = Product(
        business_id="business-1",
        name="QUESO CREMA LA SERENISIMA 290GR",
        normalized_name="queso crema la serenisima 290gr",
        brand="LA SERENISIMA",
        unit_size=Decimal("290"),
        unit="g",
        current_cost=Decimal("3100"),
        sale_price=Decimal("4300"),
    )

    [candidate] = service.match_item(item=item, products=[product])

    assert candidate.relationship_type != SupplierProductRelationship.EXACT_MATCH
    assert candidate.recommendation == "review"


class FeedbackRepository:
    def __init__(
        self,
        *,
        products: list[Product],
        offer_items: list[SupplierOfferItem],
        feedback_rules: list[ProductMatchFeedbackRule],
    ) -> None:
        self.products = products
        self.offer_items = offer_items
        self.feedback_rules = feedback_rules

    def list_products(self, *, business_id: str) -> list[Product]:
        return [product for product in self.products if product.business_id == business_id]

    def list_supplier_offer_items(
        self, *, business_id: str, supplier_offer_document_id: str
    ) -> list[SupplierOfferItem]:
        return [
            item
            for item in self.offer_items
            if item.business_id == business_id
            and item.supplier_offer_document_id == supplier_offer_document_id
        ]

    def list_product_match_feedback_rules(
        self,
        *,
        business_id: str,
        supplier_id: str,
        normalized_names: set[str],
    ) -> list[ProductMatchFeedbackRule]:
        return [
            rule
            for rule in self.feedback_rules
            if rule.supplier_id == supplier_id
            and rule.supplier_offer_item_normalized_name in normalized_names
        ]


def test_matcher_prioritizes_accepted_feedback_before_fuzzy_score() -> None:
    item = SupplierOfferItem(
        business_id="business-1",
        supplier_offer_document_id="document-1",
        supplier_id="supplier-1",
        raw_name="QUESO CREMA CREMIGAL X290GRS",
        normalized_name="queso crema cremigal x290grs",
        brand="CREMIGAL",
        unit_size=Decimal("290"),
        unit="g",
        offer_price=Decimal("2650"),
    )
    fuzzy_winner = Product(
        id="product-fuzzy",
        business_id="business-1",
        name="QUESO CREMA CREMIGAL 290GR",
        normalized_name="queso crema cremigal 290gr",
        current_cost=Decimal("3000"),
        sale_price=Decimal("4500"),
    )
    confirmed_product = Product(
        id="product-confirmed",
        business_id="business-1",
        name="QUESO CREMA LA SERENISIMA 290GR",
        normalized_name="queso crema la serenisima 290gr",
        current_cost=Decimal("3100"),
        sale_price=Decimal("4300"),
    )
    repository = FeedbackRepository(
        products=[fuzzy_winner, confirmed_product],
        offer_items=[item],
        feedback_rules=[
            ProductMatchFeedbackRule(
                supplier_id="supplier-1",
                supplier_offer_item_normalized_name="queso crema cremigal x290grs",
                candidate_product_id=confirmed_product.id,
                relationship_type=SupplierProductRelationship.COMPARABLE_ALTERNATIVE,
                accepted=True,
                created_at=item.created_at,
            )
        ],
    )
    service = ProductMatchService(repository=repository)  # type: ignore[arg-type]

    report = service.compare_supplier_offer(
        business_id="business-1",
        supplier_offer_document_id="document-1",
    )

    [candidate] = report.candidates
    assert candidate.product == confirmed_product
    assert candidate.confidence_score == Decimal("1.0000")
    assert candidate.reasons == ["confirmed_by_feedback"]


def test_pilot_candidates_filter_size_but_preserve_human_confirmation() -> None:
    item = SupplierOfferItem(
        business_id="business-1",
        supplier_offer_document_id="document-1",
        supplier_id="supplier-1",
        raw_name="ACEITE 900ML",
        normalized_name="aceite 900ml",
        unit_size=Decimal("900"),
        unit="ml",
        offer_price=Decimal("1000"),
    )
    correct = Product(
        id="correct",
        business_id="business-1",
        name="ACEITE 900ML",
        normalized_name="aceite 900ml",
        unit_size=Decimal("0.9"),
        unit="l",
    )
    wrong_size = Product(
        id="wrong",
        business_id="business-1",
        name="ACEITE 1L",
        normalized_name="aceite 1l",
        unit_size=Decimal("1"),
        unit="l",
    )
    repository = FeedbackRepository(
        products=[correct, wrong_size], offer_items=[item], feedback_rules=[]
    )
    service = ProductMatchService(repository=repository)  # type: ignore[arg-type]
    candidates = service.retrieve_pilot_candidates(
        business_id="business-1",
        supplier_offer_document_id="document-1",
    ).candidates
    assert {candidate.product.id for candidate in candidates if candidate.product} == {"correct"}
    repository.feedback_rules.append(
        ProductMatchFeedbackRule(
            supplier_id="supplier-1",
            supplier_offer_item_normalized_name="aceite 900ml",
            candidate_product_id="wrong",
            relationship_type=SupplierProductRelationship.EXACT_MATCH,
            accepted=True,
            created_at=item.created_at,
        )
    )
    [confirmed] = service.retrieve_pilot_candidates(
        business_id="business-1",
        supplier_offer_document_id="document-1",
    ).candidates
    assert confirmed.product.id == "wrong"
    assert "confirmed_by_feedback" in confirmed.reasons


def test_matcher_compares_confirmed_pack_price_per_unit() -> None:
    service = ProductMatchService(repository=None)  # type: ignore[arg-type]
    item = SupplierOfferItem(
        business_id="business-1",
        supplier_offer_document_id="document-1",
        supplier_id="supplier-1",
        raw_name="COCA COLA ZERO 2.25L X6",
        normalized_name="coca cola zero 2 25l x6",
        unit_size=Decimal("2.25"),
        unit="l",
        package_quantity=6,
        offer_price=Decimal("12000"),
        price_type=SupplierPriceType.PACK,
    )
    product = Product(
        business_id="business-1",
        name="COCA COLA ZERO 2.25L",
        normalized_name="coca cola zero 2 25l",
        unit_size=Decimal("2.25"),
        unit="l",
        current_cost=Decimal("2100"),
        sale_price=Decimal("3000"),
    )

    [candidate] = service.match_item(item=item, products=[product])

    assert item.comparable_unit_price == Decimal("2000.00")
    assert candidate.cost_difference == Decimal("100.00")
    assert candidate.recommendation == "buy"
    assert "price_normalized_by_pack=6" in candidate.reasons


def test_matcher_requires_review_instead_of_inferring_pack_from_cost_gap() -> None:
    service = ProductMatchService(repository=None)  # type: ignore[arg-type]
    item = SupplierOfferItem(
        business_id="business-1",
        supplier_offer_document_id="document-1",
        supplier_id="supplier-1",
        raw_name="COCA COLA ZERO 2.25L X6",
        normalized_name="coca cola zero 2 25l x6",
        package_quantity=6,
        offer_price=Decimal("12000"),
    )
    product = Product(
        business_id="business-1",
        name="COCA COLA ZERO 2.25L",
        normalized_name="coca cola zero 2 25l",
        current_cost=Decimal("2100"),
        sale_price=Decimal("3000"),
    )

    [candidate] = service.match_item(item=item, products=[product])

    assert candidate.cost_difference is None
    assert candidate.recommendation == "review"
    assert "possible_pack_requires_review" in candidate.reasons


def test_matcher_keeps_unknown_pack_price_in_review() -> None:
    service = ProductMatchService(repository=None)  # type: ignore[arg-type]
    item = SupplierOfferItem(
        business_id="business-1",
        supplier_offer_document_id="document-1",
        supplier_id="supplier-1",
        raw_name="COCA COLA ZERO 2.25L X6",
        normalized_name="coca cola zero 2 25l x6",
        package_quantity=6,
        offer_price=Decimal("9000"),
        price_type=SupplierPriceType.UNKNOWN,
    )
    product = Product(
        business_id="business-1",
        name="COCA COLA ZERO 2.25L",
        normalized_name="coca cola zero 2 25l",
        current_cost=Decimal("2000"),
        sale_price=Decimal("3000"),
    )

    [candidate] = service.match_item(item=item, products=[product])

    assert candidate.cost_difference is None
    assert candidate.recommendation == "review"
    assert "unknown_price_type" in candidate.reasons
    assert not any(reason.startswith("possible_pack_price=6") for reason in candidate.reasons)


def test_canonical_units_match_catalog_without_using_reader_agreement_as_match_score():
    service = ProductMatchService(repository=None)
    item = SupplierOfferItem(
        business_id="b",
        supplier_id="s",
        supplier_offer_document_id="d",
        raw_name="Arroz Gallo 1000 g",
        normalized_name="arroz gallo 1000 g",
        brand="Gallo",
        unit_size=1000,
        unit="g",
        offer_price=100,
        metadata={"agreement_score": 1.0, "resolved_by": "deterministic"},
    )
    product = Product(
        business_id="b",
        name="Arroz Gallo 1 kg",
        normalized_name="arroz gallo 1 kg",
        brand="Gallo",
        unit_size=1,
        unit="kg",
        current_cost=200,
    )
    candidate = service._score_product(item, product)
    assert "same_unit_size" in candidate.reasons
    assert candidate.confidence_score < 1
    item.metadata["resolved_by"] = "human_review"
    candidate = service._with_commercials(candidate)
    assert candidate.recommendation == "review"
    assert "reader_observation_needs_review" in candidate.reasons
