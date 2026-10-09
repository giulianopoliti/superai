from app.assistant.tools.schemas import SalesSummaryQuery, TopProductsQuery
from app.modules.catalog.resolver import CatalogResolverService


class UnresolvedSalesFilter(ValueError):
    pass


def resolve_sales_filters(
    query: TopProductsQuery | SalesSummaryQuery, resolver: CatalogResolverService, business_id: str
) -> dict[str, object]:
    included = list(
        dict.fromkeys(query.families + [term for term in (query.family, query.category) if term])
    )
    categories = []
    excluded_categories = []
    for requested, target in (
        (included, categories),
        (query.exclude_families, excluded_categories),
    ):
        for term in requested:
            resolution = resolver.resolve_category(business_id=business_id, query=term)
            if not resolution.category:
                raise UnresolvedSalesFilter(
                    f"No pude resolver la familia '{term}'. ¿Cuál querés usar?"
                )
            target.append(resolution.category)
    if set(categories) & set(excluded_categories):
        raise UnresolvedSalesFilter(
            "Hay familias incluidas y excluidas a la vez. ¿Cuál filtro uso?"
        )

    excluded_ids = set(query.exclude_product_ids)
    for term in query.exclude_product_names:
        products = resolver.resolve_product_group(business_id=business_id, query=term)
        if not products:
            raise UnresolvedSalesFilter(
                f"No pude resolver el producto '{term}'. ¿Cuál es su nombre o SKU?"
            )
        excluded_ids.update(product.id for product in products)
    return {
        "categories": categories,
        "exclude_categories": excluded_categories,
        "exclude_product_ids": excluded_ids,
        "exclude_skus": set(query.exclude_skus),
    }
