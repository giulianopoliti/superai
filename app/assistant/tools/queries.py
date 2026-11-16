import re
from datetime import timedelta

from app.assistant.tools.filters import UnresolvedSalesFilter, resolve_sales_filters
from app.assistant.tools.formatting import cost_warning, format_number
from app.assistant.tools.sales import SalesTopProductsTool
from app.assistant.tools.schemas import (
    AssistantToolName,
    AssistantToolResult,
    ComparePeriodsQuery,
    ProductHistoryQuery,
    SalesSummaryQuery,
    SearchProductsQuery,
    StockStatusQuery,
    ToolExecutionContext,
)
from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.db.repositories.sql_sales import SqlSalesRepository
from app.modules.catalog.resolver import CatalogResolverService
from app.modules.procurement.normalization import normalize_text
from app.modules.sales.analytics import compare_sales_totals


class SalesQueryTool:
    def __init__(
        self,
        name: AssistantToolName,
        repository: SqlSalesRepository,
        resolver: CatalogResolverService,
    ) -> None:
        self.name = name
        self.repository = repository
        self.resolver = resolver

    def execute(
        self, *, arguments: dict[str, object], context: ToolExecutionContext
    ) -> AssistantToolResult:
        try:
            if self.name == AssistantToolName.SALES_PRODUCT_HISTORY:
                return self._history(ProductHistoryQuery.model_validate(arguments), context)
            schema = (
                ComparePeriodsQuery
                if self.name == AssistantToolName.SALES_COMPARE_PERIODS
                else SalesSummaryQuery
            )
            query = schema.model_validate(arguments)
            if query.business_id and query.business_id != context.business_id:
                raise ValueError("business_id must match execution context")
            start, end, status = self._dates(query, context)
            filters = resolve_sales_filters(query, self.resolver, context.business_id)
            totals = self.repository.query_sales_summary(
                business_id=context.business_id, date_from=start, date_to=end, **filters
            )
            data = {
                "date_from": start,
                "date_to": end,
                "totals": totals,
                "filters": filters,
                "last_sale_date": status.last_sale_date,
            }
            requested = normalize_text(context.text or "")
            narrow_revenue = bool(re.search(
                r"cuanto vend(?:i|imos)|cuanto factur(?:e|amos)", requested
            ))
            ticket_only = "ticket promedio" in requested and "resumen" not in requested
            margin_only = (
                any(term in requested for term in ("margen", "ganancia"))
                and "resumen" not in requested
            )
            period_label = f"del {start:%d/%m/%Y} al {end:%d/%m/%Y}"
            if not totals["line_count"]:
                reply = (
                    f"No hay ventas cargadas {period_label} con esos filtros; "
                    "no puedo confirmar cuánto vendiste."
                )
            elif ticket_only:
                average = totals["average_ticket"]
                reply = (
                    f"Ticket promedio {period_label}: ${format_number(average)} "
                    f"({totals['ticket_count']} tickets)."
                    if average is not None
                    else f"No puedo calcular el ticket promedio {period_label}."
                )
            elif margin_only:
                reply = (
                    f"Margen bruto {period_label}: ${format_number(totals['gross_margin'])}."
                    if totals["margin_reliable"] else cost_warning(totals["zero_cost_line_count"])
                )
            elif narrow_revenue:
                reply = f"Ventas registradas {period_label}: ${format_number(totals['revenue'])}."
            else:
                reply = (
                    f"Ventas {period_label}: ${format_number(totals['revenue'])}; "
                    f"{totals['ticket_count']} tickets."
                )
                if totals["average_ticket"] is not None:
                    reply += f" Ticket promedio: ${format_number(totals['average_ticket'])}."
                if totals["margin_reliable"]:
                    reply += f" Margen bruto: ${format_number(totals['gross_margin'])}."
            if status.last_sale_date and end > status.last_sale_date:
                reply += f" La última venta cargada es del {status.last_sale_date:%d/%m/%Y}."
            if isinstance(query, ComparePeriodsQuery):
                previous_end = query.comparison_date_to or start - timedelta(days=1)
                previous_start = query.comparison_date_from or previous_end - (end - start)
                previous = self.repository.query_sales_summary(
                    business_id=context.business_id,
                    date_from=previous_start,
                    date_to=previous_end,
                    **filters,
                )
                changes = compare_sales_totals(totals, previous)
                data.update(
                    comparison_date_from=previous_start,
                    comparison_date_to=previous_end,
                    comparison_totals=previous,
                    changes=changes,
                    comparison_data_available=bool(totals["line_count"] and previous["line_count"]),
                )
                revenue_change = changes["revenue"]
                percent = revenue_change["percent"]
                comparison_reply = (
                    f" Comparado con {previous_start:%d/%m/%Y}–{previous_end:%d/%m/%Y}: "
                    f"variación de facturación ${format_number(revenue_change['absolute'])}"
                )
                comparison_reply += (
                    f" ({format_number(percent)}%)."
                    if percent is not None
                    else "; sin porcentaje porque el período base tiene facturación cero."
                )
                reply += (
                    comparison_reply if totals["line_count"] and previous["line_count"]
                    else " No puedo confirmar la variación: falta información de ventas "
                    "en uno de los períodos; sin porcentaje de comparación."
                )
                if previous["zero_cost_line_count"]:
                    reply += " " + cost_warning(previous["zero_cost_line_count"])
            if filters["categories"]:
                reply += " Familias incluidas: " + ", ".join(filters["categories"]) + "."
            if filters["exclude_categories"]:
                reply += " Familias excluidas: " + ", ".join(filters["exclude_categories"]) + "."
            if query.auxiliary_filters:
                reply += " Clasificación auxiliar actual: " + ", ".join(
                    query.auxiliary_filters.values()
                ) + "."
            if query.exclude_product_names:
                reply += " Productos excluidos: " + ", ".join(query.exclude_product_names) + "."
            warning = (
                cost_warning(totals["zero_cost_line_count"])
                if not narrow_revenue and not ticket_only and not margin_only else ""
            )
            return AssistantToolResult(
                name=self.name, reply=reply + (" " + warning if warning else ""), data=data
            )
        except UnresolvedSalesFilter as exc:
            return AssistantToolResult(
                name=self.name, reply=str(exc), metadata={"requires_clarification": True}
            )

    def _dates(self, query, context):
        status = self.repository.get_sales_history_status(business_id=context.business_id)
        end = query.date_to or status.last_sale_date or SalesTopProductsTool._context_date(context)
        start = query.date_from or (
            status.first_sale_date
            if getattr(query, "period", None) == "all_time" and status.first_sale_date
            else end - timedelta(days=query.days - 1)
        )
        if start > end:
            raise UnresolvedSalesFilter(
                "El inicio del período es posterior al final. ¿Qué fechas uso?"
            )
        if (end - start).days >= 365 and self.name == AssistantToolName.SALES_PRODUCT_HISTORY:
            raise UnresolvedSalesFilter(
                "Consultá el historial por producto en períodos de hasta 365 días."
            )
        return start, end, status

    def _history(self, query, context):
        resolution = self.resolver.resolve_product(
            business_id=context.business_id, query=query.product
        )
        product = resolution.product
        if product is None or (resolution.confidence < 1 and len(resolution.candidates) > 1):
            options = ", ".join(f"{p.name} (SKU {p.sku})" for p in resolution.candidates)
            raise UnresolvedSalesFilter(
                "¿Qué producto querés consultar? " + (options or query.product)
            )
        start, end, status = self._dates(query, context)
        summary = self.repository.summarize_product_sales(
            business_id=context.business_id, product_id=product.id, date_from=start, date_to=end
        )
        history = self.repository.product_sales_history(
            business_id=context.business_id, product_id=product.id, date_from=start, date_to=end
        )
        reply = (
            f"{product.name}, del {start:%d/%m/%Y} al {end:%d/%m/%Y}: "
            f"{format_number(summary.quantity_sold)} cantidad registrada; "
            f"facturación ${format_number(summary.revenue)}."
        )
        if not history:
            reply = (
                f"No hay ventas cargadas de {product.name} del {start:%d/%m/%Y} "
                f"al {end:%d/%m/%Y}; no puedo confirmar cuánto vendiste."
            )
        elif re.search(
            r"historial|por dia|dia por dia|detalle diario", normalize_text(context.text or "")
        ):
            reply += "\nDías con ventas cargadas:"
            for day in history[-31:]:
                reply += (
                    f"\n{day['date']}: {format_number(day['quantity_sold'])}; "
                    f"${format_number(day['revenue'])}."
                )
            if len(history) > 31:
                reply += (
                    "\nMostré los últimos 31 días con ventas; los datos incluyen todo el período."
                )
        warning = cost_warning(summary.zero_cost_line_count)
        return AssistantToolResult(
            name=self.name,
            reply=reply + (" " + warning if warning else ""),
            data={
                "summary": summary.model_dump(mode="json"),
                "history": history,
                "last_sale_date": status.last_sale_date,
                "history_contains_sale_days_only": True,
            },
        )


class CatalogQueryTool:
    def __init__(
        self,
        name: AssistantToolName,
        repository: SqlProcurementRepository,
        resolver: CatalogResolverService,
    ) -> None:
        self.name = name
        self.repository = repository
        self.resolver = resolver

    def execute(
        self, *, arguments: dict[str, object], context: ToolExecutionContext
    ) -> AssistantToolResult:
        schema = (
            StockStatusQuery
            if self.name == AssistantToolName.INVENTORY_STOCK_STATUS
            else SearchProductsQuery
        )
        query = schema.model_validate(arguments)
        family = None
        if query.family:
            family = self.resolver.resolve_category(
                business_id=context.business_id, query=query.family
            ).category
            if not family:
                return AssistantToolResult(
                    name=self.name,
                    reply=f"¿Qué familia es '{query.family}'?",
                    metadata={"requires_clarification": True},
                )
        products = self.repository.search_products(
            business_id=context.business_id,
            query=query.query,
            family=family,
            max_stock=getattr(query, "max_stock", None),
            limit=query.limit,
        )
        fields = (
            "id",
            "sku",
            "name",
            "category",
            "unit",
            "stock_quantity",
            "sale_price",
            "current_cost",
        )
        data = [{key: getattr(product, key) for key in fields} for product in products]
        requested = normalize_text(context.text or "")
        price_only = self.name == AssistantToolName.CATALOG_SEARCH_PRODUCTS and any(
            term in requested for term in ("precio", "cuanto cuesta", "a cuanto vendo", "costo")
        )
        stock_only = self.name == AssistantToolName.INVENTORY_STOCK_STATUS
        ambiguous = bool(query.query and len(products) > 1 and (price_only or stock_only))
        if ambiguous:
            options = "; ".join(p.name for p in products[:5])
            reply = f"Encontré varias presentaciones: {options}. ¿Cuál querés consultar?"
        else:
            lines = []
            for product in products:
                if stock_only:
                    value = (
                        f"stock {format_number(product.stock_quantity)} cantidad registrada"
                        if product.stock_quantity is not None else "stock no informado"
                    )
                elif price_only:
                    cost = "costo" in requested
                    amount = product.current_cost if cost else product.sale_price
                    label = "costo" if cost else "precio de venta"
                    value = (
                        f"{label} ${format_number(amount)}"
                        if amount is not None else f"sin {label} cargado"
                    )
                else:
                    price = (
                        f"precio ${format_number(product.sale_price)}"
                        if product.sale_price is not None else "sin precio cargado"
                    )
                    stock = (
                        f"stock {format_number(product.stock_quantity)} cantidad registrada"
                        if product.stock_quantity is not None else "stock no informado"
                    )
                    value = f"{stock}; {price}"
                lines.append(f"{product.name}: {value}.")
            reply = "\n".join(lines)
        return AssistantToolResult(
            name=self.name,
            reply=(("" if ambiguous else "Catálogo importado:\n") + reply)
            if reply
            else "No encontré productos con esos filtros.",
            data={
                "products": data,
                "family": family,
                "limit": query.limit,
                "max_stock": getattr(query, "max_stock", None),
            },
            metadata={
                "product_count": len(products), "stock_source": "imported_catalog",
                "requires_clarification": ambiguous,
            },
        )
