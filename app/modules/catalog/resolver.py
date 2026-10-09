from difflib import SequenceMatcher

from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.modules.catalog.schemas import CatalogResolution, CategoryResolution, ProductResolution
from app.modules.procurement.normalization import normalize_match_text, normalize_text
from app.modules.procurement.schemas import Product


class CatalogResolverService:
    """Resolves user-facing catalog terms into canonical database values."""

    def __init__(self, repository: SqlProcurementRepository) -> None:
        self._repository = repository

    def resolve_for_sales_query(
        self,
        *,
        business_id: str,
        category: str | None = None,
        categories: list[str] | None = None,
        exclude_product_names: list[str] | None = None,
    ) -> CatalogResolution:
        requested_categories = [term for term in categories or [] if term.strip()]
        if category and not requested_categories:
            requested_categories = [category]
        resolved_categories = [
            self.resolve_category(business_id=business_id, query=term)
            for term in requested_categories
        ]
        return CatalogResolution(
            category=resolved_categories[0] if resolved_categories else None,
            categories=resolved_categories,
            excluded_products=[
                self.resolve_product(business_id=business_id, query=term)
                for term in exclude_product_names or []
                if term.strip()
            ],
        )

    def resolve_category(self, *, business_id: str, query: str) -> CategoryResolution:
        cleaned_query = query.strip()
        normalized_query = normalize_text(cleaned_query)
        if not normalized_query:
            return CategoryResolution(requested=query)

        categories = self._repository.list_categories(business_id=business_id)
        scored = [
            (category, self._score_text(normalized_query, normalize_text(category)))
            for category in categories
        ]
        scored.sort(key=lambda item: item[1], reverse=True)
        best_category, best_score = scored[0] if scored else (None, 0)
        candidates = [category for category, score in scored[:5] if score >= 0.45]
        if best_category is None or best_score < 0.58:
            return CategoryResolution(
                requested=query,
                confidence=best_score,
                candidates=candidates,
            )
        return CategoryResolution(
            requested=query,
            category=best_category,
            confidence=best_score,
            candidates=candidates,
        )

    def resolve_product(self, *, business_id: str, query: str) -> ProductResolution:
        cleaned_query = query.strip()
        normalized_query = normalize_match_text(cleaned_query)
        if not normalized_query:
            return ProductResolution(requested=query)

        products = self._repository.list_products(business_id=business_id, active_only=True)
        scored = [
            (product, self._score_product(normalized_query, cleaned_query, product))
            for product in products
        ]
        scored.sort(key=lambda item: item[1], reverse=True)
        best_product, best_score = scored[0] if scored else (None, 0)
        candidates = [product for product, score in scored[:5] if score >= 0.45]
        if best_product is None or best_score < 0.68:
            return ProductResolution(
                requested=query,
                confidence=best_score,
                candidates=candidates,
            )
        return ProductResolution(
            requested=query,
            product=best_product,
            confidence=best_score,
            candidates=candidates,
        )

    def resolve_product_group(self, *, business_id: str, query: str) -> list[Product]:
        # Generic exclusions apply to every presentation, never just the best fuzzy match.
        normalized = normalize_text(query)
        products = self._repository.list_products(business_id=business_id, active_only=False)
        exact_identifiers = [
            product
            for product in products
            if query.strip().casefold()
            in {
                value.casefold()
                for value in (product.sku, product.barcode, product.external_product_id)
                if value
            }
        ]
        if exact_identifiers:
            return exact_identifiers
        if normalized in {"huevo", "huevos"}:
            return [product for product in products if self._is_grocery_egg(product.name)]
        terms = [term for term in normalized.split() if term not in {"de", "la", "el"}]
        matches = [
            product
            for product in products
            if terms
            and all(
                any(token.startswith(term) for token in normalize_text(product.name).split())
                for term in terms
            )
        ]
        if matches:
            return matches
        resolution = self.resolve_product(business_id=business_id, query=query)
        return [resolution.product] if resolution.product else []

    @staticmethod
    def _is_grocery_egg(name: str) -> bool:
        tokens = set(normalize_text(name).split())
        return (
            tokens in ({"huevo"}, {"huevos"})
            or bool(tokens & {"huevo", "huevos"})
            and bool(tokens & {"maple", "docena", "organicos", "blancos", "colorados", "oferta"})
        )

    @staticmethod
    def _score_product(normalized_query: str, raw_query: str, product: Product) -> float:
        identifier_values = {
            value.strip().lower()
            for value in (product.sku, product.barcode, product.external_product_id)
            if value and value.strip()
        }
        if raw_query.strip().lower() in identifier_values:
            return 1

        normalized_name = normalize_match_text(product.normalized_name or product.name)
        return CatalogResolverService._score_text(normalized_query, normalized_name)

    @staticmethod
    def _score_text(normalized_query: str, normalized_candidate: str) -> float:
        if not normalized_candidate:
            return 0
        if normalized_query == normalized_candidate:
            return 1
        if f" {normalized_query} " in f" {normalized_candidate} ":
            return 0.92

        query_tokens = set(normalized_query.split())
        candidate_tokens = set(normalized_candidate.split())
        if query_tokens and query_tokens.issubset(candidate_tokens):
            return 0.9

        prefix_matches = sum(
            1
            for query_token in query_tokens
            if any(candidate_token.startswith(query_token) for candidate_token in candidate_tokens)
        )
        prefix_score = prefix_matches / max(len(query_tokens), 1)

        token_overlap = len(query_tokens & candidate_tokens) / max(len(query_tokens), 1)
        sequence_score = SequenceMatcher(None, normalized_query, normalized_candidate).ratio()
        return max(sequence_score, token_overlap * 0.85, prefix_score * 0.88)
