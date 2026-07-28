from decimal import ROUND_HALF_UP, Decimal
from difflib import SequenceMatcher

from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.modules.procurement.normalization import normalize_match_text, normalize_text
from app.modules.procurement.schemas import (
    Product,
    ProductMatchCandidate,
    ProductMatchCandidateRecord,
    ProductMatchFeedbackRule,
    SupplierOfferComparisonReport,
    SupplierOfferItem,
    SupplierProductRelationship,
)


class ProductMatchService:
    def __init__(self, repository: SqlProcurementRepository) -> None:
        self._repository = repository

    def compare_supplier_offer(
        self,
        *,
        business_id: str,
        supplier_offer_document_id: str,
        max_candidates_per_item: int = 1,
    ) -> SupplierOfferComparisonReport:
        products = self._repository.list_products(business_id=business_id)
        offer_items = self._repository.list_supplier_offer_items(
            business_id=business_id,
            supplier_offer_document_id=supplier_offer_document_id,
        )
        feedback_rules = self._load_feedback_rules(
            business_id=business_id,
            offer_items=offer_items,
        )
        candidates: list[ProductMatchCandidate] = []
        for item in offer_items:
            item_candidates = self._match_item_with_feedback(
                item=item,
                products=products,
                feedback_rules=feedback_rules.get(item.normalized_name, []),
                limit=max_candidates_per_item,
            )
            candidates.extend(item_candidates)

        return SupplierOfferComparisonReport(
            business_id=business_id,
            supplier_offer_document_id=supplier_offer_document_id,
            candidates=candidates,
        )

    def save_supplier_offer_candidates(
        self,
        *,
        report: SupplierOfferComparisonReport,
    ) -> list[ProductMatchCandidateRecord]:
        return self._repository.replace_product_match_candidates(
            business_id=report.business_id,
            supplier_offer_document_id=report.supplier_offer_document_id,
            candidates=report.candidates,
        )

    def compare_and_save_supplier_offer(
        self,
        *,
        business_id: str,
        supplier_offer_document_id: str,
        max_candidates_per_item: int = 1,
    ) -> tuple[SupplierOfferComparisonReport, list[ProductMatchCandidateRecord]]:
        report = self.compare_supplier_offer(
            business_id=business_id,
            supplier_offer_document_id=supplier_offer_document_id,
            max_candidates_per_item=max_candidates_per_item,
        )
        records = self.save_supplier_offer_candidates(report=report)
        return report, records

    def match_item(
        self,
        *,
        item: SupplierOfferItem,
        products: list[Product],
        limit: int = 1,
        excluded_product_ids: set[str] | None = None,
    ) -> list[ProductMatchCandidate]:
        excluded_product_ids = excluded_product_ids or set()
        candidate_products = [
            product for product in products if product.id not in excluded_product_ids
        ]
        scored = [self._score_product(item, product) for product in candidate_products]
        scored.sort(key=lambda candidate: candidate.confidence_score, reverse=True)
        useful = [
            candidate for candidate in scored if candidate.confidence_score >= Decimal("0.45")
        ]

        if not useful:
            return [
                ProductMatchCandidate(
                    supplier_offer_item=item,
                    product=None,
                    relationship_type=SupplierProductRelationship.NEW_PRODUCT,
                    confidence_score=Decimal("0"),
                    reasons=["No local product candidate reached the minimum score."],
                    recommendation="review",
                )
            ]

        return [self._with_commercials(candidate) for candidate in useful[:limit]]

    def _load_feedback_rules(
        self,
        *,
        business_id: str,
        offer_items: list[SupplierOfferItem],
    ) -> dict[str, list[ProductMatchFeedbackRule]]:
        if not offer_items:
            return {}

        rules_by_name: dict[str, list[ProductMatchFeedbackRule]] = {}
        supplier_ids = {item.supplier_id for item in offer_items}
        for supplier_id in supplier_ids:
            supplier_names = {
                item.normalized_name
                for item in offer_items
                if item.supplier_id == supplier_id
            }
            rules = self._repository.list_product_match_feedback_rules(
                business_id=business_id,
                supplier_id=supplier_id,
                normalized_names=supplier_names,
            )
            for rule in rules:
                rules_by_name.setdefault(rule.supplier_offer_item_normalized_name, []).append(
                    rule
                )
        return rules_by_name

    def _match_item_with_feedback(
        self,
        *,
        item: SupplierOfferItem,
        products: list[Product],
        feedback_rules: list[ProductMatchFeedbackRule],
        limit: int,
    ) -> list[ProductMatchCandidate]:
        products_by_id = {product.id: product for product in products}
        latest_by_product_id: dict[str, ProductMatchFeedbackRule] = {}
        for rule in feedback_rules:
            if rule.candidate_product_id is None:
                continue
            latest_by_product_id.setdefault(rule.candidate_product_id, rule)

        accepted_candidates = [
            self._with_commercials(
                ProductMatchCandidate(
                    supplier_offer_item=item,
                    product=products_by_id[product_id],
                    relationship_type=rule.relationship_type,
                    confidence_score=Decimal("1.0000"),
                    reasons=["confirmed_by_feedback"],
                )
            )
            for product_id, rule in latest_by_product_id.items()
            if rule.accepted and product_id in products_by_id
        ]
        if accepted_candidates:
            return accepted_candidates[:limit]

        rejected_product_ids = {
            product_id
            for product_id, rule in latest_by_product_id.items()
            if not rule.accepted
        }
        return self.match_item(
            item=item,
            products=products,
            limit=limit,
            excluded_product_ids=rejected_product_ids,
        )

    def _score_product(self, item: SupplierOfferItem, product: Product) -> ProductMatchCandidate:
        reasons: list[str] = []
        supplier_name = normalize_match_text(item.normalized_name or item.raw_name)
        product_name = normalize_match_text(product.normalized_name or product.name)
        name_score = Decimal(str(SequenceMatcher(None, supplier_name, product_name).ratio()))
        score = name_score * Decimal("0.65")
        reasons.append(f"name_similarity={name_score.quantize(Decimal('0.01'))}")

        supplier_tokens = set(supplier_name.split())
        product_tokens = set(product_name.split())
        if supplier_tokens and product_tokens:
            token_overlap = Decimal(
                str(len(supplier_tokens & product_tokens) / len(supplier_tokens | product_tokens))
            )
            score += token_overlap * Decimal("0.20")
            reasons.append(f"token_overlap={token_overlap.quantize(Decimal('0.01'))}")

        if item.brand:
            normalized_brand = normalize_text(item.brand)
            if normalized_brand and normalized_brand in product_name:
                score += Decimal("0.10")
                reasons.append("brand_found")
            elif normalized_brand:
                score -= Decimal("0.12")
                reasons.append("brand_not_found")

        has_comparable_unit = (
            item.unit_size is not None
            and product.unit_size is not None
            and item.unit
            and product.unit
        )
        if has_comparable_unit:
            if item.unit == product.unit and item.unit_size == product.unit_size:
                score += Decimal("0.10")
                reasons.append("same_unit_size")
            elif item.unit == product.unit:
                score -= Decimal("0.10")
                reasons.append("same_unit_different_size")
            else:
                score -= Decimal("0.15")
                reasons.append("different_unit")

        score = max(Decimal("0"), min(Decimal("1"), score))
        relationship = self._relationship_for_score(score, reasons)
        return ProductMatchCandidate(
            supplier_offer_item=item,
            product=product,
            relationship_type=relationship,
            confidence_score=score.quantize(Decimal("0.0001")),
            reasons=reasons,
        )

    @staticmethod
    def _relationship_for_score(
        score: Decimal,
        reasons: list[str],
    ) -> SupplierProductRelationship:
        if score >= Decimal("0.92"):
            if "same_unit_size" in reasons:
                return SupplierProductRelationship.SAME_PRODUCT_DIFFERENT_NAME
            return SupplierProductRelationship.EXACT_MATCH
        if score >= Decimal("0.75"):
            return SupplierProductRelationship.SAME_PRODUCT_DIFFERENT_NAME
        if score >= Decimal("0.55"):
            return SupplierProductRelationship.COMPARABLE_ALTERNATIVE
        return SupplierProductRelationship.SIMILAR_BUT_NOT_EQUIVALENT

    @staticmethod
    def _with_commercials(candidate: ProductMatchCandidate) -> ProductMatchCandidate:
        product = candidate.product
        item = candidate.supplier_offer_item
        if product is None:
            return candidate

        if product.current_cost is not None:
            candidate.cost_difference = (product.current_cost - item.offer_price).quantize(
                Decimal("0.01")
            )
            if product.current_cost:
                candidate.cost_difference_percentage = (
                    candidate.cost_difference / product.current_cost * Decimal("100")
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        if product.sale_price is not None and item.offer_price:
            candidate.estimated_margin_percentage = (
                (product.sale_price - item.offer_price) / item.offer_price * Decimal("100")
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        if candidate.confidence_score < Decimal("0.75"):
            candidate.recommendation = "review"
        elif candidate.cost_difference is not None and candidate.cost_difference > 0:
            candidate.recommendation = "buy"
        elif candidate.cost_difference is not None and candidate.cost_difference <= 0:
            candidate.recommendation = "do_not_buy"
        else:
            candidate.recommendation = "review"

        return candidate
