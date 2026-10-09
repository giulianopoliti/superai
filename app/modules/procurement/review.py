from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.modules.procurement.matching import ProductMatchService
from app.modules.procurement.schemas import (
    ProductMatchCandidate,
    ProductMatchCorrectionRequest,
    ProductMatchFeedback,
    ProductMatchReviewCandidate,
    ProductMatchReviewList,
    ProductMatchReviewRequest,
    SupplierProductRelationship,
    utc_now,
)


class ProductMatchReviewService:
    def __init__(self, repository: SqlProcurementRepository) -> None:
        self._repository = repository

    def list_candidates(
        self,
        *,
        business_id: str,
        supplier_offer_document_id: str,
    ) -> ProductMatchReviewList:
        candidates = self._repository.list_product_match_candidates(
            business_id=business_id,
            supplier_offer_document_id=supplier_offer_document_id,
        )
        items_by_id = {
            item.id: item
            for item in self._repository.list_supplier_offer_items(
                business_id=business_id,
                supplier_offer_document_id=supplier_offer_document_id,
            )
        }
        product_ids = {
            candidate.product_id
            for candidate in candidates
            if candidate.product_id is not None
        }
        products_by_id = {
            product.id: product
            for product in self._repository.list_products_by_ids(
                business_id=business_id,
                product_ids=product_ids,
            )
        }
        return ProductMatchReviewList(
            business_id=business_id,
            supplier_offer_document_id=supplier_offer_document_id,
            candidates=[
                ProductMatchReviewCandidate(
                    candidate=candidate,
                    supplier_offer_item=items_by_id[candidate.supplier_offer_item_id],
                    product=products_by_id.get(candidate.product_id or ""),
                )
                for candidate in candidates
                if candidate.supplier_offer_item_id in items_by_id
            ],
        )

    def accept_candidate(
        self,
        *,
        product_match_candidate_id: str,
        request: ProductMatchReviewRequest,
    ) -> ProductMatchFeedback:
        candidate = self._get_candidate(
            business_id=request.business_id,
            product_match_candidate_id=product_match_candidate_id,
        )
        relationship = request.relationship_type or candidate.relationship_type
        return self._repository.add_product_match_feedback(
            ProductMatchFeedback(
                business_id=request.business_id,
                product_match_candidate_id=candidate.id,
                supplier_offer_item_id=candidate.supplier_offer_item_id,
                candidate_product_id=candidate.product_id,
                relationship_type=relationship,
                accepted=True,
                reviewed_by_user_id=request.reviewed_by_user_id,
                notes=request.notes,
            )
        )

    def reject_candidate(
        self,
        *,
        product_match_candidate_id: str,
        request: ProductMatchReviewRequest,
    ) -> ProductMatchFeedback:
        candidate = self._get_candidate(
            business_id=request.business_id,
            product_match_candidate_id=product_match_candidate_id,
        )
        return self._repository.add_product_match_feedback(
            ProductMatchFeedback(
                business_id=request.business_id,
                product_match_candidate_id=candidate.id,
                supplier_offer_item_id=candidate.supplier_offer_item_id,
                candidate_product_id=candidate.product_id,
                relationship_type=(
                    request.relationship_type
                    or SupplierProductRelationship.NOT_SAME_PRODUCT
                ),
                accepted=False,
                reviewed_by_user_id=request.reviewed_by_user_id,
                notes=request.notes,
            )
        )

    def correct_candidate(
        self,
        *,
        product_match_candidate_id: str,
        request: ProductMatchCorrectionRequest,
    ) -> ProductMatchFeedback:
        candidate = self._get_candidate(
            business_id=request.business_id,
            product_match_candidate_id=product_match_candidate_id,
        )
        product = self._repository.get_product(
            business_id=request.business_id,
            product_id=request.product_id,
        )
        if product is None:
            raise ValueError(f"Product not found: {request.product_id}")

        supplier_offer_item = self._repository.get_supplier_offer_item(
            business_id=request.business_id,
            supplier_offer_item_id=candidate.supplier_offer_item_id,
        )
        if supplier_offer_item is None:
            raise ValueError(f"Supplier offer item not found: {candidate.supplier_offer_item_id}")

        corrected_match = ProductMatchService._with_commercials(
            ProductMatchCandidate(
                supplier_offer_item=supplier_offer_item,
                product=product,
                relationship_type=request.relationship_type,
                confidence_score=candidate.confidence_score,
                reasons=[*candidate.reasons, "corrected_by_user"],
            )
        )
        candidate.product_id = product.id
        candidate.relationship_type = request.relationship_type
        candidate.reasons = corrected_match.reasons
        candidate.cost_difference = corrected_match.cost_difference
        candidate.cost_difference_percentage = corrected_match.cost_difference_percentage
        candidate.estimated_margin_percentage = corrected_match.estimated_margin_percentage
        candidate.recommendation = corrected_match.recommendation
        candidate.updated_at = utc_now()
        self._repository.update_product_match_candidate(candidate)

        return self._repository.add_product_match_feedback(
            ProductMatchFeedback(
                business_id=request.business_id,
                product_match_candidate_id=candidate.id,
                supplier_offer_item_id=candidate.supplier_offer_item_id,
                candidate_product_id=product.id,
                relationship_type=request.relationship_type,
                accepted=True,
                reviewed_by_user_id=request.reviewed_by_user_id,
                notes=request.notes,
            )
        )

    def _get_candidate(
        self,
        *,
        business_id: str,
        product_match_candidate_id: str,
    ):
        candidate = self._repository.get_product_match_candidate(
            business_id=business_id,
            product_match_candidate_id=product_match_candidate_id,
        )
        if candidate is None:
            raise ValueError(f"Product match candidate not found: {product_match_candidate_id}")
        return candidate
