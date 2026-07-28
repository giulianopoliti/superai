from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base
from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.modules.procurement.schemas import (
    Product,
    ProductMatchCandidate,
    ProductMatchCandidateStatus,
    ProductMatchFeedback,
    Supplier,
    SupplierOfferDocument,
    SupplierOfferItem,
    SupplierProduct,
    SupplierProductRelationship,
)


def build_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_product_can_have_multiple_supplier_prices() -> None:
    session = build_session()
    repository = SqlProcurementRepository(session)
    product = repository.add_product(
        Product(
            business_id="business-1",
            name="ACEITE CAÑUELAS GIRASOL 900ML",
            normalized_name="aceite canuelas girasol 900ml",
            brand="CAÑUELAS",
            unit_size=Decimal("900"),
            unit="ml",
            sale_price=Decimal("4200"),
            current_cost=Decimal("3120"),
        )
    )
    vital = repository.add_supplier(
        Supplier(
            business_id="business-1",
            name="Vital",
            normalized_name="vital",
        )
    )
    distributor = repository.add_supplier(
        Supplier(
            business_id="business-1",
            name="Distribuidora Norte",
            normalized_name="distribuidora norte",
        )
    )

    repository.add_supplier_product(
        SupplierProduct(
            business_id="business-1",
            supplier_id=vital.id,
            product_id=product.id,
            supplier_product_name="CAÑUELAS ACEITE 900ML",
            supplier_product_normalized_name="canuelas aceite 900ml",
            cost_price=Decimal("2990"),
        )
    )
    repository.add_supplier_product(
        SupplierProduct(
            business_id="business-1",
            supplier_id=distributor.id,
            product_id=product.id,
            supplier_product_name="ACEITE CAÑUELAS GIRASOL 900ML",
            supplier_product_normalized_name="aceite canuelas girasol 900ml",
            cost_price=Decimal("3120"),
        )
    )

    comparison = repository.compare_supplier_prices(
        business_id="business-1",
        product_id=product.id,
    )

    assert comparison is not None
    assert comparison.current_cost == Decimal("3120.00")
    assert [price.supplier_name for price in comparison.supplier_prices] == [
        "Vital",
        "Distribuidora Norte",
    ]
    assert comparison.best_supplier_price is not None
    assert comparison.best_supplier_price.cost_price == Decimal("2990.00")


def test_supplier_price_comparison_is_scoped_by_business() -> None:
    session = build_session()
    repository = SqlProcurementRepository(session)
    product = repository.add_product(
        Product(
            business_id="business-1",
            name="ARROZ OKITA 1KG",
            normalized_name="arroz okita 1kg",
        )
    )

    assert repository.compare_supplier_prices(
        business_id="business-2",
        product_id=product.id,
    ) is None


def test_product_match_candidates_can_be_replaced_and_reviewed() -> None:
    session = build_session()
    repository = SqlProcurementRepository(session)
    product = repository.add_product(
        Product(
            business_id="business-1",
            name="UVITA TINTO TETRABRICK 1L",
            normalized_name="uvita tinto tetrabrick 1l",
            sale_price=Decimal("2700"),
            current_cost=Decimal("2088"),
        )
    )
    supplier = repository.add_supplier(
        Supplier(
            business_id="business-1",
            name="Vital",
            normalized_name="vital",
        )
    )
    document = repository.add_supplier_offer_document(
        SupplierOfferDocument(
            business_id="business-1",
            supplier_id=supplier.id,
            source_filename="vital.pdf",
        )
    )
    item = repository.add_supplier_offer_items(
        [
            SupplierOfferItem(
                business_id="business-1",
                supplier_offer_document_id=document.id,
                supplier_id=supplier.id,
                raw_name="UVITA Vino t/b 1lt",
                normalized_name="uvita vino tetrabrick 1l",
                offer_price=Decimal("1469"),
            )
        ]
    )[0]
    candidate = ProductMatchCandidate(
        supplier_offer_item=item,
        product=product,
        relationship_type=SupplierProductRelationship.SAME_PRODUCT_DIFFERENT_NAME,
        confidence_score=Decimal("0.8647"),
        reasons=["brand_found", "same_unit_size"],
        cost_difference=Decimal("619"),
        cost_difference_percentage=Decimal("29.65"),
        estimated_margin_percentage=Decimal("83.80"),
        recommendation="buy",
    )

    first_records = repository.replace_product_match_candidates(
        business_id="business-1",
        supplier_offer_document_id=document.id,
        candidates=[candidate],
    )
    second_records = repository.replace_product_match_candidates(
        business_id="business-1",
        supplier_offer_document_id=document.id,
        candidates=[candidate],
    )
    saved = repository.list_product_match_candidates(
        business_id="business-1",
        supplier_offer_document_id=document.id,
    )

    assert len(first_records) == 1
    assert len(second_records) == 1
    assert len(saved) == 1
    assert saved[0].product_id == product.id
    assert saved[0].status == ProductMatchCandidateStatus.PENDING

    feedback = repository.add_product_match_feedback(
        ProductMatchFeedback(
            business_id="business-1",
            product_match_candidate_id=saved[0].id,
            supplier_offer_item_id=item.id,
            candidate_product_id=product.id,
            relationship_type=SupplierProductRelationship.SAME_PRODUCT_DIFFERENT_NAME,
            accepted=True,
            reviewed_by_user_id="user-1",
            notes="Coincide.",
        )
    )
    reviewed = repository.list_product_match_candidates(
        business_id="business-1",
        supplier_offer_document_id=document.id,
    )
    rules = repository.list_product_match_feedback_rules(
        business_id="business-1",
        supplier_id=supplier.id,
        normalized_names={item.normalized_name},
    )

    assert feedback.accepted is True
    assert reviewed[0].status == ProductMatchCandidateStatus.ACCEPTED
    assert len(rules) == 1
    assert rules[0].supplier_offer_item_normalized_name == item.normalized_name
    assert rules[0].candidate_product_id == product.id
    assert rules[0].accepted is True
    assert (
        repository.list_product_match_feedback_rules(
            business_id="business-2",
            supplier_id=supplier.id,
            normalized_names={item.normalized_name},
        )
        == []
    )
