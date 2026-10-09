from datetime import date, timedelta
from zoneinfo import ZoneInfo

from app.assistant.tools.filters import UnresolvedSalesFilter, resolve_sales_filters
from app.assistant.tools.formatting import cost_warning, format_number
from app.assistant.tools.schemas import (
    AssistantToolName,
    AssistantToolResult,
    ToolExecutionContext,
    TopProductsQuery,
    TopProductsToolResponse,
)
from app.db.repositories.sql_sales import SqlSalesRepository
from app.modules.catalog.resolver import CatalogResolverService
from app.modules.catalog.schemas import CatalogResolution
from app.modules.sales.schemas import SalesHistoryStatus


class SalesTopProductsTool:
    name = AssistantToolName.SALES_TOP_PRODUCTS

    def __init__(
        self,
        sales_repository: SqlSalesRepository,
        catalog_resolver: CatalogResolverService | None = None,
    ) -> None:
        self._sales_repository = sales_repository
        self._catalog_resolver = catalog_resolver

    def execute(
        self,
        *,
        arguments: dict[str, object],
        context: ToolExecutionContext,
    ) -> AssistantToolResult:
        try:
            response = self.run_query(TopProductsQuery.model_validate(arguments), context=context)
        except UnresolvedSalesFilter as exc:
            return AssistantToolResult(
                name=self.name, reply=str(exc), metadata={"requires_clarification": True}
            )
        return AssistantToolResult(
            name=self.name,
            reply=response.reply,
            data=response.model_dump(mode="json"),
            metadata={
                "product_count": len(response.products),
                "date_from": response.date_from.isoformat(),
                "date_to": response.date_to.isoformat(),
                "period": response.period,
                "sort": response.sort,
                "rank_from": response.rank_from,
                "rank_to": response.rank_to,
                "resolved_category": response.resolved_category,
                "resolved_categories": response.resolved_categories,
                "resolved_excluded_product_ids": response.resolved_excluded_product_ids,
                "unresolved_exclusions": response.unresolved_exclusions,
            },
        )

    def run_query(
        self,
        query: TopProductsQuery,
        *,
        context: ToolExecutionContext,
    ) -> TopProductsToolResponse:
        if query.business_id and query.business_id != context.business_id:
            raise ValueError("business_id must match the execution context")
        business_id = context.business_id
        sales_status = self._sales_repository.get_sales_history_status(business_id=business_id)
        date_to = query.date_to or sales_status.last_sale_date or self._context_date(context)
        if query.period == "all_time" and sales_status.first_sale_date is not None:
            date_from = query.date_from or sales_status.first_sale_date
        else:
            date_from = query.date_from or date_to - timedelta(days=query.days - 1)
        if date_from > date_to:
            raise UnresolvedSalesFilter(
                "El inicio del período es posterior al final. ¿Qué fechas uso?"
            )
        resolution = self._resolve_catalog_filters(query=query, business_id=business_id)
        if self._catalog_resolver is not None:
            filters = resolve_sales_filters(query, self._catalog_resolver, business_id)
        else:
            if query.exclude_product_names or query.exclude_families:
                raise UnresolvedSalesFilter("Necesito el catálogo para resolver las exclusiones.")
            filters = {
                "categories": self._requested_categories(query),
                "exclude_categories": [],
                "exclude_product_ids": set(query.exclude_product_ids),
                "exclude_skus": set(query.exclude_skus),
            }
        resolved_categories = filters["categories"]
        resolved_category = resolved_categories[0] if resolved_categories else None
        resolved_excluded_product_ids = filters["exclude_product_ids"]
        unresolved_exclusions = []
        all_products = self._sales_repository.top_products(
            business_id=business_id,
            date_from=date_from,
            date_to=date_to,
            sort=query.sort,
            limit=self._fetch_limit(query),
            min_quantity=query.min_quantity,
            **filters,
        )
        products = self._slice_ranked_products(all_products, query)
        response = TopProductsToolResponse(
            business_id=business_id,
            date_from=date_from,
            date_to=date_to,
            sort=query.sort,
            limit=query.limit,
            period=query.period,
            rank_from=query.rank_from,
            rank_to=query.rank_to,
            exclude_product_names=query.exclude_product_names,
            exclude_product_ids=query.exclude_product_ids,
            exclude_skus=query.exclude_skus,
            category=query.category,
            family=query.family,
            families=query.families,
            excluded_families=filters["exclude_categories"],
            resolved_category=resolved_category,
            resolved_categories=resolved_categories,
            resolved_excluded_product_ids=sorted(resolved_excluded_product_ids),
            unresolved_exclusions=unresolved_exclusions,
            min_quantity=query.min_quantity,
            products=products,
            reply=self._format_reply(
                products=products,
                date_from=date_from,
                date_to=date_to,
                sort=query.sort,
                sales_status=sales_status,
                excluded_terms=query.exclude_product_names,
                resolution=resolution,
                requested_categories=self._requested_categories(query),
                rank_from=query.rank_from,
            ),
        )
        if response.excluded_families:
            response.reply += "\nFamilias excluidas: " + ", ".join(response.excluded_families) + "."
        warning = cost_warning(sum(product.zero_cost_line_count for product in products))
        if warning:
            response.reply += "\n" + warning
        return response

    @staticmethod
    def _fetch_limit(query: TopProductsQuery) -> int:
        if query.rank_to is not None:
            return query.rank_to
        if query.rank_from is not None:
            return min(query.rank_from + query.limit - 1, 50)
        return query.limit

    @staticmethod
    def _slice_ranked_products(products, query: TopProductsQuery):
        if query.rank_from is None and query.rank_to is None:
            return products
        start = (query.rank_from or 1) - 1
        end = query.rank_to if query.rank_to is not None else start + query.limit
        return products[start:end]

    def _resolve_catalog_filters(
        self,
        *,
        query: TopProductsQuery,
        business_id: str,
    ) -> CatalogResolution | None:
        if self._catalog_resolver is None:
            return None
        return self._catalog_resolver.resolve_for_sales_query(
            business_id=business_id,
            category=query.category or query.family,
            categories=self._requested_categories(query),
            exclude_product_names=query.exclude_product_names,
        )

    @staticmethod
    def _context_date(context: ToolExecutionContext) -> date:
        if context.timestamp is None:
            return date.today()
        if context.timestamp.tzinfo is None:
            return context.timestamp.date()
        return context.timestamp.astimezone(ZoneInfo("America/Buenos_Aires")).date()

    @staticmethod
    def _clean_terms(terms: list[str]) -> list[str]:
        return [term.strip() for term in terms if term.strip()]

    @staticmethod
    def _requested_categories(query: TopProductsQuery) -> list[str]:
        requested = [term for term in query.families if term.strip()]
        if query.category:
            requested.append(query.category)
        if query.family:
            requested.append(query.family)

        seen: set[str] = set()
        unique_requested: list[str] = []
        for term in requested:
            key = term.strip().casefold()
            if key and key not in seen:
                seen.add(key)
                unique_requested.append(term.strip())
        return unique_requested

    @staticmethod
    def _format_reply(
        *,
        products,
        date_from: date,
        date_to: date,
        sort: str,
        sales_status: SalesHistoryStatus,
        excluded_terms: list[str],
        resolution: CatalogResolution | None = None,
        requested_categories: list[str] | None = None,
        rank_from: int | None = None,
    ) -> str:
        if sales_status.total_sale_lines == 0:
            return "Todavia no hay ventas cargadas para responder esa consulta."
        if not products:
            filters = SalesTopProductsTool._format_filter_suffix(
                resolution=resolution,
                requested_categories=requested_categories or [],
            )
            return (
                f"No encontre productos vendidos del {date_from:%d/%m} al {date_to:%d/%m}{filters}."
            )

        sort_label = {
            "quantity": "mas vendidos",
            "revenue": "con mas facturacion",
            "margin": "con mas margen",
        }.get(sort, "mas vendidos")
        lines = [f"Productos {sort_label} del {date_from:%d/%m} al {date_to:%d/%m}:"]
        start_index = rank_from or 1
        for index, product in enumerate(products, start=start_index):
            name = product.product_name or product.external_product_id or "Sin nombre"
            if sort == "revenue":
                metric = f"${format_number(product.revenue)}"
            elif sort == "margin":
                metric = f"${format_number(product.gross_margin)}"
            else:
                metric = f"{format_number(product.quantity_sold)} cantidad registrada"
            lines.append(f"{index}. {name}: {metric}")
        cleaned_exclusions = [term for term in excluded_terms if term.strip()]
        if cleaned_exclusions:
            lines.append("Exclui: " + ", ".join(cleaned_exclusions) + ".")
        resolved_categories = SalesTopProductsTool._resolved_category_names(resolution)
        if resolved_categories:
            lines.append(f"Familias: {', '.join(resolved_categories)}.")
        return "\n".join(lines)

    @staticmethod
    def _resolved_category_names(resolution: CatalogResolution | None) -> list[str]:
        if resolution is None:
            return []
        names = [
            category_resolution.category
            for category_resolution in resolution.categories
            if category_resolution.category
        ]
        if not names and resolution.category and resolution.category.category:
            names = [resolution.category.category]
        return names

    @staticmethod
    def _format_filter_suffix(
        *,
        resolution: CatalogResolution | None,
        requested_categories: list[str],
    ) -> str:
        resolved_categories = SalesTopProductsTool._resolved_category_names(resolution)
        categories = resolved_categories or requested_categories
        if not categories:
            return ""
        return " para " + ", ".join(categories)
