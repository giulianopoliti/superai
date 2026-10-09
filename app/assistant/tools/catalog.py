from app.assistant.tools.schemas import (
    AssistantToolName,
    AssistantToolResult,
    ResolvedFamily,
    ResolvedProduct,
    ResolveFamiliesQuery,
    ResolveFamiliesResponse,
    ResolveProductsQuery,
    ResolveProductsResponse,
    ToolExecutionContext,
)
from app.modules.catalog.resolver import CatalogResolverService


class CatalogResolveFamiliesTool:
    name = AssistantToolName.CATALOG_RESOLVE_FAMILIES

    def __init__(self, catalog_resolver: CatalogResolverService) -> None:
        self._catalog_resolver = catalog_resolver

    def execute(
        self,
        *,
        arguments: dict[str, object],
        context: ToolExecutionContext,
    ) -> AssistantToolResult:
        query = ResolveFamiliesQuery.model_validate(arguments)
        families = [
            self._catalog_resolver.resolve_category(
                business_id=context.business_id,
                query=term,
            )
            for term in query.terms
            if term.strip()
        ]
        response = ResolveFamiliesResponse(
            families=[
                ResolvedFamily(
                    requested=family.requested,
                    family=family.category,
                    confidence=family.confidence,
                    candidates=family.candidates,
                )
                for family in families
            ],
            reply=self._format_reply(
                [
                    ResolvedFamily(
                        requested=family.requested,
                        family=family.category,
                        confidence=family.confidence,
                        candidates=family.candidates,
                    )
                    for family in families
                ]
            ),
        )
        return AssistantToolResult(
            name=self.name,
            reply=response.reply,
            data=response.model_dump(mode="json"),
            metadata={
                "resolved_count": sum(1 for family in response.families if family.family),
                "requested_count": len(query.terms),
            },
        )

    @staticmethod
    def _format_reply(families: list[ResolvedFamily]) -> str:
        if not families:
            return "No recibi familias para resolver."
        resolved = [
            f"{family.requested} -> {family.family}"
            for family in families
            if family.family is not None
        ]
        unresolved = [family.requested for family in families if family.family is None]
        lines = []
        if resolved:
            lines.append("Familias resueltas: " + ", ".join(resolved) + ".")
        if unresolved:
            lines.append("No pude resolver: " + ", ".join(unresolved) + ".")
        return " ".join(lines)


class CatalogResolveProductsTool:
    name = AssistantToolName.CATALOG_RESOLVE_PRODUCTS

    def __init__(self, catalog_resolver: CatalogResolverService) -> None:
        self._catalog_resolver = catalog_resolver

    def execute(
        self,
        *,
        arguments: dict[str, object],
        context: ToolExecutionContext,
    ) -> AssistantToolResult:
        query = ResolveProductsQuery.model_validate(arguments)
        resolutions = [
            self._catalog_resolver.resolve_product(
                business_id=context.business_id,
                query=term,
            )
            for term in query.terms
            if term.strip()
        ]
        resolved_products = [
            ResolvedProduct(
                requested=resolution.requested,
                product_id=resolution.product.id if resolution.product else None,
                product_name=resolution.product.name if resolution.product else None,
                sku=resolution.product.sku if resolution.product else None,
                confidence=resolution.confidence,
                candidates=[
                    {
                        "product_id": candidate.id,
                        "product_name": candidate.name,
                        "sku": candidate.sku,
                    }
                    for candidate in resolution.candidates[: query.limit]
                ],
            )
            for resolution in resolutions
        ]
        response = ResolveProductsResponse(
            products=resolved_products,
            reply=self._format_reply(resolved_products),
        )
        return AssistantToolResult(
            name=self.name,
            reply=response.reply,
            data=response.model_dump(mode="json"),
            metadata={
                "resolved_count": sum(1 for product in response.products if product.product_id),
                "requested_count": len(query.terms),
            },
        )

    @staticmethod
    def _format_reply(products: list[ResolvedProduct]) -> str:
        if not products:
            return "No recibi productos para resolver."
        resolved = [
            f"{product.requested} -> {product.product_name}"
            for product in products
            if product.product_name is not None
        ]
        unresolved = [product.requested for product in products if product.product_name is None]
        lines = []
        if resolved:
            lines.append("Productos resueltos: " + ", ".join(resolved) + ".")
        if unresolved:
            lines.append("No pude resolver: " + ", ".join(unresolved) + ".")
        return " ".join(lines)
