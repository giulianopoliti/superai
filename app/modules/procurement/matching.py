import logging
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from difflib import SequenceMatcher
from time import monotonic
from types import SimpleNamespace

from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.modules.catalog.product_view import product_search_names
from app.modules.procurement.analysis_contracts import IdentityCandidate, OfferInterpretation
from app.modules.procurement.identity import (
    IDENTITY_RULE_VERSION,
    auxiliary_identity_gaps,
    can_accept_identity,
    canonical_identity_equal,
    catalog_variant_ambiguity,
    identity_conflicts,
    identity_discriminator_gaps,
    inconsistent_size,
    is_multi_product_offer,
    physical_size,
    retrieval_identity_tokens,
)
from app.modules.procurement.normalization import normalize_match_text, normalize_text
from app.modules.procurement.pack_review import PackReviewDecision
from app.modules.procurement.pricing import (
    SupplierPriceType,
    is_likely_pack_price,
    normalize_offer_price,
)
from app.modules.procurement.schemas import (
    Product,
    ProductMatchCandidate,
    ProductMatchCandidateRecord,
    ProductMatchFeedbackRule,
    SupplierOfferComparisonReport,
    SupplierOfferItem,
    SupplierProductRelationship,
)

logger = logging.getLogger("uvicorn.error")


class ProductMatchService:
    def __init__(self, repository: SqlProcurementRepository) -> None:
        self._repository = repository

    def compare_supplier_offer(
        self,
        *,
        business_id: str,
        supplier_offer_document_id: str,
        max_candidates_per_item: int = 1,
        trace_id: str = "none",
    ) -> SupplierOfferComparisonReport:
        started_at = monotonic()
        stage_started_at = started_at
        products = self._repository.list_products(business_id=business_id)
        now = monotonic()
        logger.info(
            "catalog_match request_id=%s stage=products_loaded duration_ms=%.1f "
            "product_count=%s",
            trace_id,
            (now - stage_started_at) * 1000,
            len(products),
        )
        stage_started_at = now
        offer_items = self._repository.list_supplier_offer_items(
            business_id=business_id,
            supplier_offer_document_id=supplier_offer_document_id,
        )
        if any(
            item.business_id != business_id
            or item.supplier_offer_document_id != supplier_offer_document_id
            for item in offer_items
        ):
            raise ValueError("Offer does not match requested business/document")
        now = monotonic()
        logger.info(
            "catalog_match request_id=%s stage=offer_items_loaded duration_ms=%.1f "
            "item_count=%s",
            trace_id,
            (now - stage_started_at) * 1000,
            len(offer_items),
        )
        stage_started_at = now
        feedback_rules = self._load_feedback_rules(
            business_id=business_id,
            offer_items=offer_items,
        )
        now = monotonic()
        logger.info(
            "catalog_match request_id=%s stage=feedback_loaded duration_ms=%.1f "
            "rule_group_count=%s",
            trace_id,
            (now - stage_started_at) * 1000,
            len(feedback_rules),
        )
        stage_started_at = now
        candidates: list[ProductMatchCandidate] = []
        for item in offer_items:
            item_candidates = self._match_item_with_feedback(
                item=item,
                products=products,
                feedback_rules=feedback_rules.get((item.supplier_id, item.normalized_name), []),
                limit=max_candidates_per_item,
            )
            candidates.extend(item_candidates)

        logger.info(
            "catalog_match request_id=%s stage=completed duration_ms=%.1f "
            "total_duration_ms=%.1f candidate_count=%s",
            trace_id,
            (monotonic() - stage_started_at) * 1000,
            (monotonic() - started_at) * 1000,
            len(candidates),
        )

        return SupplierOfferComparisonReport(
            business_id=business_id,
            supplier_offer_document_id=supplier_offer_document_id,
            candidates=candidates,
        )

    def retrieve_pilot_candidates(
        self, *, business_id: str, supplier_offer_document_id: str, top_k: int = 5
    ) -> SupplierOfferComparisonReport:
        """Read-only closed candidate set, including unresolved feedback conflicts."""
        if top_k < 1:
            raise ValueError("top_k must be positive")
        items = self._repository.list_supplier_offer_items(
            business_id=business_id, supplier_offer_document_id=supplier_offer_document_id
        )
        candidates = self.retrieve_identity_candidates(
            business_id=business_id, offer_items=items, internal_top_k=top_k,
        )
        return SupplierOfferComparisonReport(
            business_id=business_id,
            supplier_offer_document_id=supplier_offer_document_id,
            candidates=candidates,
        )

    def retrieve_identity_candidates(
        self, *, business_id: str, offer_items: list[SupplierOfferItem], internal_top_k: int = 5,
    ) -> list[ProductMatchCandidate]:
        """Load the catalog once for provisional offers, without saving candidates.

        The internal limit is independent of the public report limit (default 1).
        Feedback conflicts are audit records, not admissible choices for JEV.
        """
        if internal_top_k < 1:
            raise ValueError("internal_top_k must be positive")
        if any(item.business_id != business_id for item in offer_items):
            raise ValueError("Offer tenant does not match business_id")
        products = self._repository.list_products(business_id=business_id)
        rules = self._load_feedback_rules(business_id=business_id, offer_items=offer_items)
        candidates = []
        for item in offer_items:
            candidates.extend(self._match_item_with_feedback(
                item=item, products=products,
                feedback_rules=rules.get((item.supplier_id, item.normalized_name), []),
                limit=internal_top_k, compatible_only=True,
            ))
        return candidates

    def retrieve_analysis_candidates(
        self, *, business_id: str, supplier_id: str, offers: list[OfferInterpretation],
        internal_top_k: int = 5,
        feedback_conflicts: dict[str, list[str]] | None = None,
    ) -> dict[str, list[IdentityCandidate]]:
        """V2 retrieval also accepts missing-price offers; no fictitious legacy item.

        Optional conflict collector lets the integrator attach reasons even if a
        reviewed product no longer exists. Original feedback is never modified.
        """
        if internal_top_k < 1:
            raise ValueError("internal_top_k must be positive")
        if any(offer.business_id != business_id for offer in offers):
            raise ValueError("Offer tenant does not match business_id")
        if len({offer.offer_id for offer in offers}) != len(offers):
            raise ValueError("Duplicate provisional offer IDs")
        products = {
            product.id: product
            for product in self._repository.list_products(business_id=business_id)
            if product.business_id == business_id
        }
        names = {normalize_text(offer.raw_name) for offer in offers if offer.raw_name}
        rules = self._repository.list_product_match_feedback_rules(
            business_id=business_id, supplier_id=supplier_id, normalized_names=names,
        ) if names else []
        search_tokens = {
            p.id: retrieval_identity_tokens(SimpleNamespace(
                raw_name=p.name, brand=p.brand, unit_size=p.unit_size, unit=p.unit,
            )) for p in products.values()
        }
        results = {}
        for offer in offers:
            normalized_name = normalize_text(offer.raw_name)
            if not offer.raw_name:
                results[offer.offer_id] = []
                continue
            latest = {}
            for rule in sorted(rules, key=lambda rule: rule.created_at, reverse=True):
                if (rule.supplier_id == supplier_id
                        and rule.supplier_offer_item_normalized_name == normalized_name
                        and rule.candidate_product_id is not None):
                    latest.setdefault(rule.candidate_product_id, rule)
            accepted = [rule for rule in latest.values() if rule.accepted]
            if accepted:
                reviewed = []
                audit_reasons = []
                for rule in accepted:
                    product = products.get(rule.candidate_product_id)
                    conflicts = identity_conflicts(offer, product) if product else [
                        "feedback_product_unavailable"
                    ]
                    alternative = rule.relationship_type == (
                        SupplierProductRelationship.COMPARABLE_ALTERNATIVE
                    )
                    if alternative:
                        conflicts = [c for c in conflicts if c not in {
                            "brand_conflict", "variant_conflict",
                        }]
                    elif rule.relationship_type not in {
                        SupplierProductRelationship.EXACT_MATCH,
                        SupplierProductRelationship.SAME_PRODUCT_DIFFERENT_NAME,
                    }:
                        conflicts.append("feedback_not_identity")
                    if len(accepted) > 1:
                        conflicts.append("multiple_accepted_feedback")
                    audit_reasons.extend(conflicts)
                    if product is not None:
                        reviewed.append(IdentityCandidate(
                            candidate_id=product.id, product_id=product.id, business_id=business_id,
                            name=product.name, brand=product.brand, auxiliary=product.auxiliary,
                            unit_size=product.unit_size,
                            unit=product.unit, reason_codes=[
                                "confirmed_by_feedback", *conflicts,
                                *(["human_accepted_alternative"] if alternative else []),
                            ],
                        ))
                if feedback_conflicts is not None and audit_reasons:
                    feedback_conflicts[offer.offer_id] = list(dict.fromkeys(audit_reasons))
                results[offer.offer_id] = reviewed[:internal_top_k]
                continue
            ranked = []
            family = []
            query = retrieval_identity_tokens(offer)
            brand_tokens = set(normalize_text(offer.brand).split())
            size = physical_size(offer.unit_size, offer.unit)
            for product in products.values():
                if product.id in latest and not latest[product.id].accepted:
                    continue
                product_tokens = search_tokens[product.id]
                # Cheap identity-only retrieval before fuzzy ranking. A named brand
                # cannot be rescued by a similar other-brand description.
                if brand_tokens and not brand_tokens <= product_tokens:
                    continue
                if not brand_tokens and not (
                    {t for t in query if not t.isdigit() and t not in {"g", "ml", "l", "kg", "de"}}
                    & product_tokens
                ):
                    continue
                conflicts = identity_conflicts(offer, product)
                if conflicts:
                    continue
                anchored = bool(
                    brand_tokens and brand_tokens <= product_tokens
                    and size and size == physical_size(product.unit_size, product.unit)
                )
                if anchored:
                    family.append(product)
                score, reasons = self._score_identity(offer, product)
                if anchored:
                    reasons = [*reasons, "retrieval_brand_size"]
                    # Rank the search projection, independent of catalog costs.
                    score = max(score, Decimal(len(query & product_tokens)) / max(len(query), 1))
                if anchored or score >= Decimal("0.45") or canonical_identity_equal(offer, product):
                    ranked.append((score, product, reasons))
            ranked.sort(key=lambda row: (
                not canonical_identity_equal(offer, row[1]), -row[0], row[1].id,
            ))
            exact_count = sum(canonical_identity_equal(offer, row[1]) for row in ranked)
            candidates = []
            for index, (_score, product, reasons) in enumerate(ranked[:internal_top_k]):
                gaps = identity_discriminator_gaps(offer, product, family)
                reasons = [*reasons, *gaps]
                if len(ranked) > internal_top_k:
                    reasons.append("retrieval_truncated")
                if is_multi_product_offer(offer):
                    reasons.append("multi_product_offer")
                ambiguous = catalog_variant_ambiguity(offer, product, list(products.values()))
                if ambiguous:
                    reasons = [*reasons, "catalog_variant_ambiguity"]
                if (index == 0 and exact_count == 1 and canonical_identity_equal(offer, product)
                        and not ambiguous and not gaps and not is_multi_product_offer(offer)):
                    reasons = [*reasons, "auto_accepted_identity_v1", IDENTITY_RULE_VERSION]
                candidates.append(IdentityCandidate(
                    candidate_id=product.id, product_id=product.id, business_id=business_id,
                    name=product.name, brand=product.brand, auxiliary=product.auxiliary,
                    unit_size=product.unit_size,
                    unit=product.unit, reason_codes=reasons,
                ))
            results[offer.offer_id] = candidates
        return results

    @staticmethod
    def _pilot_compatible(item: SupplierOfferItem, product: Product) -> bool:
        return not identity_conflicts(item, product)

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
        if limit < 1:
            raise ValueError("limit must be positive")
        excluded_product_ids = excluded_product_ids or set()
        analysis = item.metadata.get("analysis_v2")
        interpreted = analysis.get("interpretation") if isinstance(analysis, dict) else None
        if isinstance(interpreted, dict) and interpreted.get("identity_scope") == "product_group":
            # The offer metadata retains the selected IDs and inclusion status.
            # A comparison refresh must not turn a group back into one fuzzy SKU.
            return [ProductMatchCandidate(
                supplier_offer_item=item, product=None,
                relationship_type=SupplierProductRelationship.UNKNOWN,
                confidence_score=Decimal(0), recommendation="review",
                reasons=["compatible_product_group", "group_promotion_scope_requires_review"],
            )]
        resolved_id = (
            interpreted.get("product_id")
            if isinstance(interpreted, dict) and interpreted.get("identity_status") == "confirmed"
            else None
        )
        candidate_products = [
            product for product in products
            if product.id not in excluded_product_ids
            and product.business_id == item.business_id and product.active
            and (not resolved_id or product.id == resolved_id)
        ]
        observed_brand = set(normalize_text(item.brand).split()) if isinstance(interpreted, dict) else set()
        if observed_brand and not resolved_id:
            # The persisted suggestion must respect the same named brand as
            # analysis retrieval, even when catalog brand is only in its name.
            candidate_products = [
                product for product in candidate_products
                if observed_brand <= set(normalize_text(f"{product.brand or ''} {product.name}").split())
            ]
        if resolved_id and not candidate_products:
            return [ProductMatchCandidate(
                supplier_offer_item=item, product=None,
                relationship_type=SupplierProductRelationship.SIMILAR_BUT_NOT_EQUIVALENT,
                confidence_score=Decimal(0), recommendation="review",
                reasons=["resolved_product_unavailable"],
            )]
        scored = []
        identities = {}
        for product in candidate_products:
            conflicts = identity_conflicts(item, product)
            exact = canonical_identity_equal(item, product, known_conflicts=conflicts)
            identities[product.id] = exact
            scored.append(self._score_product(item, product, exact=exact, conflicts=conflicts))
        scored.sort(key=lambda candidate: (
            candidate.product.id == resolved_id,
            identities[candidate.product.id], candidate.confidence_score,
        ), reverse=True)
        useful = [
            candidate for candidate in scored if candidate.confidence_score >= Decimal("0.45")
            or identities[candidate.product.id]
            or candidate.product.id == resolved_id
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

        useful[0].auto_accepted = can_accept_identity(
            useful[0], scored[1] if len(scored) > 1 else None
        )
        if catalog_variant_ambiguity(item, useful[0].product, candidate_products):
            useful[0].auto_accepted = False
            useful[0].reasons.append("catalog_variant_ambiguity")
        return [self._with_commercials(candidate) for candidate in useful[:limit]]

    def _load_feedback_rules(
        self,
        *,
        business_id: str,
        offer_items: list[SupplierOfferItem],
    ) -> dict[tuple[str, str], list[ProductMatchFeedbackRule]]:
        if not offer_items:
            return {}

        rules_by_name: dict[tuple[str, str], list[ProductMatchFeedbackRule]] = {}
        supplier_ids = {item.supplier_id for item in offer_items}
        for supplier_id in supplier_ids:
            supplier_names = {
                item.normalized_name for item in offer_items if item.supplier_id == supplier_id
            }
            rules = self._repository.list_product_match_feedback_rules(
                business_id=business_id,
                supplier_id=supplier_id,
                normalized_names=supplier_names,
            )
            for rule in rules:
                rules_by_name.setdefault(
                    (supplier_id, rule.supplier_offer_item_normalized_name), []
                ).append(rule)
        return rules_by_name

    def _match_item_with_feedback(
        self,
        *,
        item: SupplierOfferItem,
        products: list[Product],
        feedback_rules: list[ProductMatchFeedbackRule],
        limit: int,
        compatible_only: bool = False,
    ) -> list[ProductMatchCandidate]:
        if limit < 1:
            raise ValueError("limit must be positive")
        products_by_id = {
            product.id: product for product in products if product.business_id == item.business_id
        }
        latest_by_product_id: dict[str, ProductMatchFeedbackRule] = {}
        for rule in sorted(feedback_rules, key=lambda rule: rule.created_at, reverse=True):
            if (rule.supplier_id != item.supplier_id
                    or rule.supplier_offer_item_normalized_name != item.normalized_name):
                continue
            if rule.candidate_product_id is None:
                continue
            latest_by_product_id.setdefault(rule.candidate_product_id, rule)

        accepted_rules = [rule for rule in latest_by_product_id.values() if rule.accepted]
        accepted_candidates = []
        for rule in accepted_rules:
            product = products_by_id.get(rule.candidate_product_id)
            conflicts = (
                identity_conflicts(item, product) if product else ["feedback_product_unavailable"]
            )
            identity_relationship = rule.relationship_type in {
                SupplierProductRelationship.EXACT_MATCH,
                SupplierProductRelationship.SAME_PRODUCT_DIFFERENT_NAME,
            }
            if rule.relationship_type == SupplierProductRelationship.COMPARABLE_ALTERNATIVE:
                conflicts = [c for c in conflicts if c not in {
                    "brand_conflict", "variant_conflict",
                }]
            elif not identity_relationship:
                conflicts.append("feedback_not_identity")
            if len(accepted_rules) > 1:
                conflicts.append("multiple_accepted_feedback")
            if item.metadata.get("resolved_by") == "human_review":
                conflicts.append("reader_observation_needs_review")
            accepted_candidates.append(self._with_commercials(ProductMatchCandidate(
                    supplier_offer_item=item,
                    product=product,
                    relationship_type=rule.relationship_type,
                    confidence_score=Decimal("1.0000"),
                    reasons=["confirmed_by_feedback", *conflicts],
                    auto_accepted=not conflicts and identity_relationship,
            )))
        if accepted_candidates:
            return accepted_candidates[:limit]

        rejected_product_ids = {
            product_id for product_id, rule in latest_by_product_id.items() if not rule.accepted
        }
        return self.match_item(
            item=item,
            products=[
                product for product in products_by_id.values()
                if not compatible_only or self._pilot_compatible(item, product)
            ],
            limit=limit,
            excluded_product_ids=rejected_product_ids,
        )

    def _score_product(
        self, item: SupplierOfferItem, product: Product, *, exact=None, conflicts=None,
    ) -> ProductMatchCandidate:
        score, reasons = self._score_identity(item, product)
        if conflicts is None:
            conflicts = identity_conflicts(item, product)
        if exact is None:
            exact = canonical_identity_equal(item, product, known_conflicts=conflicts)
        if product.auxiliary and exact:
            score = max(score, Decimal("0.9500"))
            reasons.append("auxiliary_canonical_identity")
        reasons.extend(auxiliary_identity_gaps(item, product))
        relationship = self._relationship_for_score(score, reasons)
        if conflicts:
            relationship = SupplierProductRelationship.NOT_SAME_PRODUCT
            reasons.extend(conflicts)
        return ProductMatchCandidate(
            supplier_offer_item=item,
            product=product,
            relationship_type=relationship,
            confidence_score=score,
            reasons=reasons,
        )

    @staticmethod
    def _score_identity(item: SupplierOfferItem | OfferInterpretation, product: Product):
        reasons: list[str] = []
        supplier_name = normalize_match_text(
            getattr(item, "normalized_name", None) or item.raw_name,
        )
        supplier_tokens = set(supplier_name.split())
        # Original and normalized names often normalize to the same text. Score each once.
        names = dict.fromkeys(normalize_match_text(name) for name in product_search_names(product))
        comparisons = []
        for name in names:
            tokens = set(name.split())
            ratio = SequenceMatcher(None, supplier_name, name).ratio()
            overlap = len(supplier_tokens & tokens) / max(len(supplier_tokens | tokens), 1)
            comparisons.append((name, ratio, overlap))
        product_name, ratio, overlap = max(
            comparisons, key=lambda row: row[1] * 0.65 + row[2] * 0.20,
        )
        if product.auxiliary:
            reasons.append("auxiliary_catalog=" + product.auxiliary.interpretation_id)
        name_score = Decimal(str(ratio))
        score = name_score * Decimal("0.65")
        reasons.append(f"name_similarity={name_score.quantize(Decimal('0.01'))}")

        product_tokens = set(product_name.split())
        if supplier_tokens and product_tokens:
            token_overlap = Decimal(str(overlap))
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
        if inconsistent_size(product.name, product.unit_size, product.unit):
            has_comparable_unit = False
            score -= Decimal("0.20")
            reasons.append("catalog_presentation_inconsistent")
        if has_comparable_unit:
            # Canonical reader observations may express kg/l as g/ml.
            item_size, item_unit = physical_size(item.unit_size, item.unit)
            product_size, product_unit = physical_size(product.unit_size, product.unit)
            if item_unit == product_unit and item_size == product_size:
                score += Decimal("0.10")
                reasons.append("same_unit_size")
            elif item_unit == product_unit:
                score -= Decimal("0.10")
                reasons.append("same_unit_different_size")
            else:
                score -= Decimal("0.15")
                reasons.append("different_unit")

        score = max(Decimal("0"), min(Decimal("1"), score))
        return score.quantize(Decimal("0.0001")), reasons

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
    def _confirmed_price_review(item: SupplierOfferItem, interpretation: dict) -> bool:
        """Existing trusted review metadata may confirm a pending basis, not invent an amount."""
        review = item.metadata.get("pack_review")
        if not isinstance(review, dict) or review.get("status") != "confirmed":
            return False
        try:
            decision = PackReviewDecision.model_validate(review.get("decision"))
            observed = Decimal(str(interpretation.get("offer_price")))
        except (ValueError, TypeError, InvalidOperation):
            return False
        if (not observed.is_finite() or observed <= 0 or observed != item.offer_price
                or decision.confidence < 0.90 or not decision.evidence.strip()
                or decision.price_type != item.price_type
                or decision.package_quantity != item.package_quantity):
            return False
        if decision.price_type == "unit":
            return decision.package_quantity is None
        return (decision.price_type == "pack" and decision.package_quantity is not None
                and 2 <= decision.package_quantity <= 1000)

    @staticmethod
    def _with_commercials(candidate: ProductMatchCandidate) -> ProductMatchCandidate:
        product = candidate.product
        item = candidate.supplier_offer_item
        candidate.cost_difference = None
        candidate.cost_difference_percentage = None
        candidate.estimated_margin_percentage = None
        candidate.recommendation = "review"
        human_identity = any(reason in candidate.reasons for reason in (
            "corrected_by_user", "confirmed_by_feedback",
        )) and candidate.relationship_type in {
            SupplierProductRelationship.EXACT_MATCH,
            SupplierProductRelationship.SAME_PRODUCT_DIFFERENT_NAME,
        }
        human_alternative = any(reason in candidate.reasons for reason in (
            "corrected_by_user", "confirmed_by_feedback",
        )) and candidate.relationship_type == SupplierProductRelationship.COMPARABLE_ALTERNATIVE
        gaps = (
            auxiliary_identity_gaps(item, product) + identity_discriminator_gaps(item, product)
            if product else []
        )
        conflicts = identity_conflicts(item, product) if product is not None else []
        if human_alternative:
            conflicts = [c for c in conflicts if c not in {"brand_conflict", "variant_conflict"}]
            candidate.auto_accepted = False
        if conflicts:
            candidate.auto_accepted = False
            candidate.reasons = list(dict.fromkeys([*candidate.reasons, *conflicts]))
        elif human_identity and not any(code in candidate.reasons for code in (
            "multiple_accepted_feedback", "feedback_product_unavailable", "feedback_not_identity",
        )):
            # ReviewService constructs a corrected candidate with default False.
            # Its old fuzzy score describes the prior suggestion, not human certainty.
            candidate.auto_accepted = True
        analysis_review_required = False
        if "analysis_v2" in item.metadata:
            analysis = item.metadata["analysis_v2"]
            interpretation = analysis.get("interpretation") if isinstance(analysis, dict) else None
            interpretation = interpretation if isinstance(interpretation, dict) else {}
            human_override = human_identity and candidate.auto_accepted and not conflicts
            identity_confirmed = bool(
                product is not None and not conflicts and not gaps and not human_alternative
                and (
                    "catalog_variant_ambiguity" not in candidate.reasons
                    or "identity_confirmed_by_decisions" in interpretation.get("reason_codes", [])
                )
                and interpretation.get("identity_status") == "confirmed"
                and interpretation.get("product_id") == product.id
            )
            candidate.auto_accepted = identity_confirmed or human_override
            price_confirmed = interpretation.get("price_status") == "confirmed" or (
                ProductMatchService._confirmed_price_review(item, interpretation)
            )
            analysis_review_required = not candidate.auto_accepted or not price_confirmed
            if (analysis_review_required
                    and "analysis_policy_review_required" not in candidate.reasons):
                candidate.reasons.append("analysis_policy_review_required")
        if item.metadata.get("resolved_by") == "human_review":
            candidate.recommendation = "review"
            candidate.reasons.append("reader_observation_needs_review")
            return candidate
        if product is None:
            return candidate

        # Catalog costs/sale prices follow the legacy ARS convention. A foreign
        # currency amount cannot support savings, margins or pack-price heuristics.
        if item.currency.strip().upper() != "ARS":
            if "currency_not_comparable" not in candidate.reasons:
                candidate.reasons.append("currency_not_comparable")
            return candidate

        if analysis_review_required:
            return candidate

        analysis = item.metadata.get("analysis_v2")
        validation = analysis.get("commercial_validation", {}) if isinstance(analysis, dict) else {}
        if validation and not validation.get("tax_basis_confirmed"):
            candidate.reasons.append("tax_basis_unverified")
            return candidate

        if inconsistent_size(product.name, product.unit_size, product.unit):
            candidate.reasons.append("catalog_presentation_inconsistent")
            candidate.recommendation = "review"
            return candidate
        if item.metadata.get("pack_review", {}).get("status") == "needs_review":
            candidate.reasons.append("pack_needs_review")
            candidate.recommendation = "review"
            return candidate

        comparable_price = item.comparable_unit_price
        if item.price_type in {SupplierPriceType.UNIT, SupplierPriceType.UNKNOWN} and (
            is_likely_pack_price(
                offer_price=item.offer_price,
                current_unit_cost=product.current_cost,
                package_quantity=item.package_quantity,
            )
        ):
            if not item.metadata.get("pack_review", {}).get("status") == "confirmed":
                candidate.reasons.append("possible_pack_requires_review")
                candidate.recommendation = "review"
                return candidate

        if comparable_price is None:
            reason = (
                "missing_package_quantity"
                if item.price_type == SupplierPriceType.PACK
                else "unknown_price_type"
            )
            if reason not in candidate.reasons:
                candidate.reasons.append(reason)
            if is_likely_pack_price(
                offer_price=item.offer_price,
                current_unit_cost=product.current_cost,
                package_quantity=item.package_quantity,
            ):
                estimated_unit_price = normalize_offer_price(
                    offer_price=item.offer_price,
                    package_quantity=item.package_quantity,
                    price_type=SupplierPriceType.PACK,
                )
                suggestion = (
                    "possible_pack_price="
                    f"{item.package_quantity};estimated_unit_price={estimated_unit_price}"
                )
                if suggestion not in candidate.reasons:
                    candidate.reasons.append(suggestion)
            candidate.recommendation = "review"
            return candidate

        if item.price_type == SupplierPriceType.PACK:
            reason = f"price_normalized_by_pack={item.package_quantity}"
            if reason not in candidate.reasons:
                candidate.reasons.append(reason)

        if not candidate.auto_accepted and not (human_alternative and not conflicts):
            candidate.recommendation = "review"
            candidate.cost_difference = None
            candidate.cost_difference_percentage = None
            candidate.estimated_margin_percentage = None
            return candidate

        if product.current_cost is not None:
            candidate.cost_difference = (product.current_cost - comparable_price).quantize(
                Decimal("0.01")
            )
            if product.current_cost:
                candidate.cost_difference_percentage = (
                    candidate.cost_difference / product.current_cost * Decimal("100")
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        if product.sale_price is not None and comparable_price:
            candidate.estimated_margin_percentage = (
                (product.sale_price - comparable_price) / comparable_price * Decimal("100")
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        if human_alternative:
            candidate.recommendation = "review"
        elif candidate.confidence_score < Decimal("0.75") and not human_identity:
            candidate.recommendation = "review"
        elif candidate.cost_difference is not None and candidate.cost_difference > 0:
            candidate.recommendation = "buy"
        elif candidate.cost_difference is not None and candidate.cost_difference <= 0:
            candidate.recommendation = "do_not_buy"
        else:
            candidate.recommendation = "review"

        return candidate
