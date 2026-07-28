from decimal import Decimal

from app.modules.procurement.matching import ProductMatchService
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
