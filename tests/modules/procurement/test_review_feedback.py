from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base
from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.modules.procurement.matching import ProductMatchService
from app.modules.procurement.review import ProductMatchReviewService
from app.modules.procurement.schemas import (
    Product,
    ProductMatchCandidate,
    ProductMatchCorrectionRequest,
    ProductMatchReviewRequest,
    Supplier,
    SupplierOfferDocument,
    SupplierOfferItem,
    SupplierProductRelationship,
)


def build_repository() -> SqlProcurementRepository:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)
    return SqlProcurementRepository(session)


def add_offer_item(
    repository: SqlProcurementRepository,
    *,
    supplier: Supplier,
    source_filename: str,
) -> tuple[SupplierOfferDocument, SupplierOfferItem]:
    document = repository.add_supplier_offer_document(
        SupplierOfferDocument(
            business_id=supplier.business_id,
            supplier_id=supplier.id,
            source_filename=source_filename,
        )
    )
    item = repository.add_supplier_offer_items(
        [
            SupplierOfferItem(
                business_id=supplier.business_id,
                supplier_offer_document_id=document.id,
                supplier_id=supplier.id,
                raw_name="PLAYADITO X10",
                normalized_name="playadito x10",
                offer_price=Decimal("10000"),
            )
        ]
    )[0]
    return document, item


def test_corrected_match_is_reused_for_next_offer_from_same_supplier() -> None:
    repository = build_repository()
    matcher = ProductMatchService(repository)
    reviewer = ProductMatchReviewService(repository)
    wrong_product = repository.add_product(
        Product(
            business_id="business-1",
            name="PLAYADITO TRADICIONAL X10",
            normalized_name="playadito tradicional x10",
        )
    )
    corrected_product = repository.add_product(
        Product(
            business_id="business-1",
            name="PLAYADITO SUAVE X10",
            normalized_name="playadito suave x10",
        )
    )
    supplier = repository.add_supplier(
        Supplier(
            business_id="business-1",
            name="Proveedor A",
            normalized_name="proveedor a",
        )
    )
    first_document, first_item = add_offer_item(
        repository,
        supplier=supplier,
        source_filename="first.txt",
    )
    [candidate] = repository.replace_product_match_candidates(
        business_id="business-1",
        supplier_offer_document_id=first_document.id,
        candidates=[
            ProductMatchCandidate(
                supplier_offer_item=first_item,
                product=wrong_product,
                relationship_type=SupplierProductRelationship.SAME_PRODUCT_DIFFERENT_NAME,
                confidence_score=Decimal("0.80"),
            )
        ],
    )
    reviewer.correct_candidate(
        product_match_candidate_id=candidate.id,
        request=ProductMatchCorrectionRequest(
            business_id="business-1",
            product_id=corrected_product.id,
        ),
    )

    second_document, _ = add_offer_item(
        repository,
        supplier=supplier,
        source_filename="second.txt",
    )
    report = matcher.compare_supplier_offer(
        business_id="business-1",
        supplier_offer_document_id=second_document.id,
    )

    [reused] = report.candidates
    assert reused.product is not None
    assert reused.product.id == corrected_product.id
    assert reused.confidence_score == Decimal("1.0000")
    assert "confirmed_by_feedback" in reused.reasons


def test_rejected_product_is_excluded_but_other_candidate_remains_available() -> None:
    repository = build_repository()
    matcher = ProductMatchService(repository)
    reviewer = ProductMatchReviewService(repository)
    rejected_product = repository.add_product(
        Product(
            business_id="business-1",
            name="PLAYADITO X10",
            normalized_name="playadito x10",
        )
    )
    alternative_product = repository.add_product(
        Product(
            business_id="business-1",
            name="PLAYADITO SUAVE X10",
            normalized_name="playadito suave x10",
        )
    )
    supplier = repository.add_supplier(
        Supplier(
            business_id="business-1",
            name="Proveedor A",
            normalized_name="proveedor a",
        )
    )
    first_document, first_item = add_offer_item(
        repository,
        supplier=supplier,
        source_filename="first.txt",
    )
    [candidate] = repository.replace_product_match_candidates(
        business_id="business-1",
        supplier_offer_document_id=first_document.id,
        candidates=[
            ProductMatchCandidate(
                supplier_offer_item=first_item,
                product=rejected_product,
                relationship_type=SupplierProductRelationship.EXACT_MATCH,
                confidence_score=Decimal("0.99"),
            )
        ],
    )
    reviewer.reject_candidate(
        product_match_candidate_id=candidate.id,
        request=ProductMatchReviewRequest(business_id="business-1"),
    )

    second_document, _ = add_offer_item(
        repository,
        supplier=supplier,
        source_filename="second.txt",
    )
    report = matcher.compare_supplier_offer(
        business_id="business-1",
        supplier_offer_document_id=second_document.id,
    )

    [replacement] = report.candidates
    assert replacement.product is not None
    assert replacement.product.id == alternative_product.id
    assert replacement.product.id != rejected_product.id


def test_feedback_rule_does_not_cross_supplier_or_business() -> None:
    repository = build_repository()
    matcher = ProductMatchService(repository)
    reviewer = ProductMatchReviewService(repository)
    exact_product = repository.add_product(
        Product(
            business_id="business-1",
            name="PLAYADITO X10",
            normalized_name="playadito x10",
        )
    )
    corrected_product = repository.add_product(
        Product(
            business_id="business-1",
            name="PLAYADITO SUAVE X10",
            normalized_name="playadito suave x10",
        )
    )
    first_supplier = repository.add_supplier(
        Supplier(
            business_id="business-1",
            name="Proveedor A",
            normalized_name="proveedor a",
        )
    )
    first_document, first_item = add_offer_item(
        repository,
        supplier=first_supplier,
        source_filename="first.txt",
    )
    [candidate] = repository.replace_product_match_candidates(
        business_id="business-1",
        supplier_offer_document_id=first_document.id,
        candidates=[
            ProductMatchCandidate(
                supplier_offer_item=first_item,
                product=exact_product,
                relationship_type=SupplierProductRelationship.EXACT_MATCH,
                confidence_score=Decimal("0.99"),
            )
        ],
    )
    reviewer.correct_candidate(
        product_match_candidate_id=candidate.id,
        request=ProductMatchCorrectionRequest(
            business_id="business-1",
            product_id=corrected_product.id,
        ),
    )

    second_supplier = repository.add_supplier(
        Supplier(
            business_id="business-1",
            name="Proveedor B",
            normalized_name="proveedor b",
        )
    )
    second_document, _ = add_offer_item(
        repository,
        supplier=second_supplier,
        source_filename="second.txt",
    )
    [other_supplier_match] = matcher.compare_supplier_offer(
        business_id="business-1",
        supplier_offer_document_id=second_document.id,
    ).candidates

    other_business_product = repository.add_product(
        Product(
            business_id="business-2",
            name="PLAYADITO X10",
            normalized_name="playadito x10",
        )
    )
    other_business_supplier = repository.add_supplier(
        Supplier(
            business_id="business-2",
            name="Proveedor A",
            normalized_name="proveedor a",
        )
    )
    other_business_document, _ = add_offer_item(
        repository,
        supplier=other_business_supplier,
        source_filename="third.txt",
    )
    [other_business_match] = matcher.compare_supplier_offer(
        business_id="business-2",
        supplier_offer_document_id=other_business_document.id,
    ).candidates

    assert other_supplier_match.product is not None
    assert other_supplier_match.product.id == exact_product.id
    assert "confirmed_by_feedback" not in other_supplier_match.reasons
    assert other_business_match.product is not None
    assert other_business_match.product.id == other_business_product.id
    assert "confirmed_by_feedback" not in other_business_match.reasons


def test_recomparison_preserves_reviewed_candidate_and_feedback() -> None:
    repository = build_repository()
    product = repository.add_product(
        Product(business_id="business-1", name="PLAYADITO X10", normalized_name="playadito x10")
    )
    supplier = repository.add_supplier(
        Supplier(business_id="business-1", name="Proveedor", normalized_name="proveedor")
    )
    document, item = add_offer_item(repository, supplier=supplier, source_filename="offer.pdf")
    candidate = ProductMatchCandidate(
        supplier_offer_item=item,
        product=product,
        relationship_type=SupplierProductRelationship.EXACT_MATCH,
        confidence_score=Decimal("0.99"),
    )
    [saved] = repository.replace_product_match_candidates(
        business_id="business-1",
        supplier_offer_document_id=document.id,
        candidates=[candidate],
    )
    ProductMatchReviewService(repository).accept_candidate(
        product_match_candidate_id=saved.id,
        request=ProductMatchReviewRequest(business_id="business-1"),
    )
    [recomputed] = repository.replace_product_match_candidates(
        business_id="business-1",
        supplier_offer_document_id=document.id,
        candidates=[candidate],
    )
    assert recomputed.id == saved.id
    assert recomputed.status.value == "accepted"
    assert (
        repository.get_product_match_candidate(
            business_id="business-1", product_match_candidate_id=saved.id
        )
        is not None
    )


def test_automatic_acceptance_is_persisted_without_human_feedback_and_reassessed():
    repository = build_repository()
    product = repository.add_product(Product(
        business_id="business-1", name="PLAYADITO X10", normalized_name="playadito x10",
    ))
    supplier = repository.add_supplier(Supplier(
        business_id="business-1", name="Proveedor", normalized_name="proveedor",
    ))
    document, item = add_offer_item(repository, supplier=supplier, source_filename="offer.pdf")
    matcher = ProductMatchService(repository)
    report, [saved] = matcher.compare_and_save_supplier_offer(
        business_id="business-1", supplier_offer_document_id=document.id,
    )
    assert report.candidates[0].auto_accepted
    assert saved.status.value == "accepted"
    assert "auto_accepted_identity_v1" in saved.reasons
    assert repository.list_product_match_feedback_rules(
        business_id="business-1", supplier_id=supplier.id, normalized_names={item.normalized_name},
    ) == []
    # A new ambiguous catalog row revokes automatic acceptance on recomparison.
    repository.add_product(product.model_copy(update={"id": "duplicate"}))
    _, [recomputed] = matcher.compare_and_save_supplier_offer(
        business_id="business-1", supplier_offer_document_id=document.id,
    )
    assert recomputed.status.value == "pending"
    # Human acceptance remains authoritative, even with ambiguous catalog rows.
    ProductMatchReviewService(repository).accept_candidate(
        product_match_candidate_id=recomputed.id,
        request=ProductMatchReviewRequest(business_id="business-1"),
    )
    _, [confirmed] = matcher.compare_and_save_supplier_offer(
        business_id="business-1", supplier_offer_document_id=document.id,
    )
    assert confirmed.id == recomputed.id
    assert confirmed.status.value == "accepted"
