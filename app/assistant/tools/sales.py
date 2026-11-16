from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.assistant.intent_router import IGNORE_STOCK
from app.assistant.tools.filters import UnresolvedSalesFilter, resolve_sales_filters
from app.assistant.tools.formatting import cost_warning, format_number
from app.assistant.tools.schemas import (
    AssistantToolName,
    AssistantToolResult,
    RankingComparison,
    RankingComparisonRow,
    ToolExecutionContext,
    TopProductsQuery,
    TopProductsToolResponse,
)
from app.db.repositories.sql_sales import SqlSalesRepository
from app.modules.catalog.resolver import CatalogResolverService
from app.modules.catalog.schemas import CatalogResolution
from app.modules.procurement.normalization import normalize_text
from app.modules.sales.schemas import ProductSalesSummary, SalesHistoryStatus


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
                "auxiliary_filters": response.auxiliary_filters,
                "supplier": response.supplier,
                "supplier_filter_applied": response.supplier_filter_applied,
                "supplier_fallback_reason": response.supplier_fallback_reason,
                "include_irrelevant_products": response.include_irrelevant_products,
                "preference_excluded_product_count": response.preference_excluded_product_count,
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
        if query.rank_to and query.rank_to - (query.rank_from or 1) + 1 > 100:
            raise UnresolvedSalesFilter(
                "Puedo mostrar hasta 100 productos por mensaje. ¿Qué tramo querés ver primero?"
            )
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
        if IGNORE_STOCK.search(normalize_text(context.text)):
            query = query.model_copy(update={"show_stock": False})
        if query.growth:
            if (date_to - date_from).days >= 365:
                raise UnresolvedSalesFilter(
                    "Para calcular subas, elegí un rango de hasta 365 días."
                )
            query = query.model_copy(update={"sort": "quantity", "show_stock": False})
        compare = bool(query.compare or query.comparison_date_from)
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
        if query.auxiliary_filters:
            filters["auxiliary_filters"] = query.auxiliary_filters
        if query.supplier:
            filters["supplier"] = query.supplier
        preference_ids = set()
        if not query.include_irrelevant_products:
            preference_ids = set(
                self._sales_repository.list_excluded_sales_ranking_product_ids(
                    business_id=business_id
                )
            )
            filters["exclude_product_ids"] = set(filters["exclude_product_ids"]) | preference_ids
        resolved_categories = filters["categories"]
        resolved_category = resolved_categories[0] if resolved_categories else None
        resolved_excluded_product_ids = filters["exclude_product_ids"]
        unresolved_exclusions = []
        offset = (query.rank_from or 1) - 1
        page_size = query.rank_to - offset if query.rank_to is not None else query.limit
        query_args = {
            "business_id": business_id,
            "date_from": date_from,
            "date_to": date_to,
            "sort": query.sort,
            "min_quantity": query.min_quantity,
        }
        fetch_limit = None if query.growth else page_size + 1
        fetch_offset = 0 if query.growth else offset
        products = self._sales_repository.top_products(
            **query_args, limit=fetch_limit, offset=fetch_offset, **filters,
        )
        supplier_filter_applied = bool(query.supplier)
        supplier_fallback_reason = None
        if query.supplier and query.supplier_optional and not products and not (
            fetch_offset and self._sales_repository.top_products(**query_args, limit=1, **filters)
        ):
            filters.pop("supplier")
            products = self._sales_repository.top_products(
                **query_args, limit=fetch_limit, offset=fetch_offset, **filters,
            )
            supplier_filter_applied = False
            supplier_fallback_reason = "no_matching_sales"
        has_more = not query.growth and len(products) > page_size
        if not query.growth:
            products = products[:page_size]
        if not query.show_stock or query.growth:
            products = [product.model_copy(update={"current_stock": None}) for product in products]
        response = TopProductsToolResponse(
            business_id=business_id,
            date_from=date_from,
            date_to=date_to,
            sort=query.sort,
            growth=query.growth,
            limit=page_size,
            period=query.period,
            rank_from=query.rank_from,
            rank_to=query.rank_to,
            exclude_product_names=query.exclude_product_names,
            exclude_product_ids=query.exclude_product_ids,
            exclude_skus=query.exclude_skus,
            category=query.category,
            family=query.family,
            supplier=query.supplier,
            supplier_filter_applied=supplier_filter_applied,
            supplier_fallback_reason=supplier_fallback_reason,
            auxiliary_filters=query.auxiliary_filters,
            families=query.families,
            excluded_families=filters["exclude_categories"],
            include_irrelevant_products=query.include_irrelevant_products,
            preference_excluded_product_ids=sorted(preference_ids),
            preference_excluded_product_count=len(preference_ids),
            resolved_category=resolved_category,
            resolved_categories=resolved_categories,
            resolved_excluded_product_ids=sorted(resolved_excluded_product_ids),
            unresolved_exclusions=unresolved_exclusions,
            min_quantity=query.min_quantity,
            products=products,
            reply="" if query.growth else self._format_reply(
                products=products,
                date_from=date_from,
                date_to=date_to,
                sort=query.sort,
                sales_status=sales_status,
                excluded_terms=query.exclude_product_names,
                resolution=resolution,
                requested_categories=self._requested_categories(query),
                rank_from=query.rank_from,
                show_rows=not compare,
            ),
        )
        if query.growth:
            response.comparison, response.products, response.reply = self._growth(
                query, response, offset, page_size, self._context_date(context),
            )
            if resolved_categories:
                response.reply += "\nFamilias: " + ", ".join(resolved_categories) + "."
        if response.excluded_families:
            response.reply += "\nFamilias excluidas: " + ", ".join(response.excluded_families) + "."
        if preference_ids:
            response.reply += (
                f"\nSe excluyeron {len(preference_ids)} productos marcados como poco relevantes."
            )
        if query.supplier:
            if supplier_filter_applied:
                response.reply += f"\nProveedor: {query.supplier} (productos vinculados)."
            else:
                response.reply += (
                    f"\nNo encontré ventas con vínculo al proveedor {query.supplier} "
                    "para esos filtros y período. Mostré el ranking sin filtrar por proveedor."
                )
        if query.auxiliary_filters:
            response.reply += "\nClasificación auxiliar actual: " + ", ".join(
                query.auxiliary_filters.values()
            ) + "."
        if query.sort == "margin" or (not compare and not query.growth):
            warning = cost_warning(sum(product.zero_cost_line_count for product in products))
            if warning:
                response.reply += "\n" + warning
        if not query.growth and compare:
            response.comparison, comparison_reply = self._compare(query, response, sales_status)
            response.reply += "\n\n" + comparison_reply
        if has_more:
            response.reply += "\nPodés pedir «mostrame 10 más» para continuar el ranking."
        return response

    def _compare(
        self, query: TopProductsQuery, response: TopProductsToolResponse,
        sales_status: SalesHistoryStatus,
    ) -> tuple[RankingComparison, str]:
        end = query.comparison_date_to or response.date_from - timedelta(days=1)
        start = query.comparison_date_from or end - (response.date_to - response.date_from)
        products = response.products
        available, totals = self._sales_repository.ranking_comparison_totals(
            business_id=response.business_id,
            date_from=start,
            date_to=end,
            external_product_ids={p.external_product_id for p in products if p.external_product_id},
            sort=query.sort,
        )
        metric, label = {
            "quantity": ("quantity_sold", "cantidad vendida"),
            "revenue": ("revenue", "facturación"),
            "margin": ("gross_margin", "margen bruto"),
        }[query.sort]
        rows = []
        reply = [f"Comparación por {label} con el {start:%d/%m/%Y} al {end:%d/%m/%Y}."]
        if not available:
            reply.append("No hay ventas cargadas para el período de comparación.")
        if products:
            reply.append("Anterior → actual (variación).")
        if (end - start) != (response.date_to - response.date_from):
            reply.append("Los períodos tienen distinta duración. Se muestran totales.")
        incomplete_range = (
            (sales_status.first_sale_date and start < sales_status.first_sale_date)
            or (sales_status.last_sale_date and end > sales_status.last_sale_date)
        )
        if available and incomplete_range:
            reply.append("El período de comparación excede las fechas con ventas cargadas.")
        if (
            products and sales_status.last_sale_date
            and response.date_to > sales_status.last_sale_date
        ):
            reply.append(
                f"Última fecha con ventas cargadas: {sales_status.last_sale_date:%d/%m/%Y}."
            )
        for index, product in enumerate(products, response.rank_from or 1):
            prior, missing_cost = totals.get(product.external_product_id, (None, 0))
            if prior is None and available and not incomplete_range and product.external_product_id:
                prior = Decimal(0)
            current = getattr(product, metric)
            previous = prior
            if query.sort == "margin":
                current = None if product.zero_cost_line_count else current
                previous = None if missing_cost else previous
            delta = current - previous if current is not None and previous is not None else None
            percent = (
                round(delta * 100 / previous, 2)
                if delta is not None and previous is not None and previous > 0 else None
            )
            name = product.product_name or product.sku or product.external_product_id
            rows.append(RankingComparisonRow(
                product_id=product.product_id,
                external_product_id=product.external_product_id,
                product_name=name,
                current=current,
                previous=previous,
                absolute_change=delta,
                percent_change=percent,
                current_stock=product.current_stock,
                unit=product.unit,
            ))
            old = format_number(previous) if previous is not None else "s/d"
            new = format_number(current) if current is not None else "s/d"
            change = f" ({'+' if percent > 0 else ''}{format_number(percent)}%)" if (
                percent is not None
            ) else ""
            stock = (
                f" · stock de catálogo: {format_number(product.current_stock)}"
                if product.current_stock is not None else ""
            )
            reply.append(f"{index}. {name} · {old} → {new}{change}{stock}.")
        return RankingComparison(
            date_from=start, date_to=end, metric=metric, data_available=available, rows=rows
        ), "\n".join(reply)

    def _growth(
        self, query: TopProductsQuery, response: TopProductsToolResponse,
        offset: int, page_size: int, today: date,
    ) -> tuple[RankingComparison, list[ProductSalesSummary], str]:
        start, end = response.date_from, response.date_to
        if query.comparison_date_from and query.comparison_date_to:
            periods = [(query.comparison_date_from, query.comparison_date_to)]
        else:
            duration = (end - start).days + 1
            step = timedelta(days=((duration + 6) // 7) * 7)
            periods = [(start - step * week, end - step * week)
                       for week in range(1, query.baseline_weeks + 1)]
        if any(first <= end and last >= start for first, last in periods):
            raise UnresolvedSalesFilter("Elegí un período de comparación que no se superponga.")
        current_days = self._sales_repository.sales_days(
            business_id=response.business_id, date_from=start, date_to=end,
        )
        comparison = RankingComparison(
            date_from=min(first for first, _ in periods),
            date_to=max(last for _, last in periods),
            metric="quantity_sold", data_available=False, rows=[], baseline_periods=[],
        )
        reply = [f"Subas de ventas · {start:%d/%m/%Y} al {end:%d/%m/%Y}."]
        if len(current_days) != (end - start).days + 1:
            reply.append(
                "No puedo calcular subas para este rango: faltan días con ventas registradas."
            )
            return comparison, [], "\n".join(reply)
        if end == today:
            reply.append("El día de hoy puede tener ventas parciales.")
        ids = {p.external_product_id for p in response.products if p.external_product_id}
        totals = {}
        baseline_days = 0
        for first, last in periods:
            day_count = (last - first).days + 1
            days = self._sales_repository.sales_days(
                business_id=response.business_id, date_from=first, date_to=last,
            )
            if len(days) != day_count:
                continue
            _, values = self._sales_repository.ranking_comparison_totals(
                business_id=response.business_id, date_from=first, date_to=last,
                external_product_ids=ids, sort="quantity",
            )
            for product_id, (quantity, _) in values.items():
                totals[product_id] = totals.get(product_id, Decimal(0)) + quantity
            comparison.baseline_periods.append((first, last))
            baseline_days += day_count
        if not baseline_days:
            reply.append("No puedo calcular subas: faltan ventas en los períodos de referencia.")
            return comparison, [], "\n".join(reply)
        comparison.data_available = True
        comparison.date_from = min(first for first, _ in comparison.baseline_periods)
        comparison.date_to = max(last for _, last in comparison.baseline_periods)
        ranges = ", ".join(
            f"{first:%d/%m}–{last:%d/%m}" for first, last in comparison.baseline_periods
        )
        reply.append(f"Referencia: {ranges}. Base ajustada a {len(current_days)} días.")
        if len(periods) != len(comparison.baseline_periods):
            reply.append("Omití períodos con días sin ventas registradas.")
        ranked = []
        for product in response.products:
            if not product.external_product_id or product.quantity_sold <= 0:
                continue
            prior = totals.get(product.external_product_id, Decimal(0))
            expected = prior * len(current_days) / baseline_days
            delta = product.quantity_sold - expected
            if delta > 0:
                ranked.append((delta, expected, product, prior))
        ranked.sort(key=lambda row: (-row[0], -row[2].quantity_sold, row[2].external_product_id))
        products = []
        for index, (delta, expected, product, prior) in enumerate(
            ranked[offset:offset + page_size], offset + 1,
        ):
            name = product.product_name or product.sku or product.external_product_id
            percent = round(delta * 100 / expected, 2) if expected > 0 else None
            comparison.rows.append(RankingComparisonRow(
                product_id=product.product_id, external_product_id=product.external_product_id,
                product_name=name, previous=prior, current=product.quantity_sold,
                absolute_change=round(delta, 3), percent_change=percent,
                current_stock=None, unit=product.unit, baseline_equivalent=round(expected, 3),
            ))
            products.append(product)
            increase = f" (+{format_number(percent)}%)" if percent is not None else ""
            reply.append(
                f"{index}. {name} · {format_number(expected)} → "
                f"{format_number(product.quantity_sold)} · +{format_number(delta)}{increase}."
            )
        if not products:
            reply.append("No encontré productos con subas en ese tramo del ranking.")
        elif len(ranked) > offset + page_size:
            reply.append("Podés pedir «mostrame 10 más» para continuar el ranking.")
        reply.append(
            "Estos productos son prioridad para revisar por demanda. "
            "Para confirmar urgencia o cantidad de compra harían falta existencias confiables "
            "y plazo de entrega."
        )
        return comparison, products, "\n".join(reply)

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
        show_rows: bool = True,
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
        for index, product in enumerate(products if show_rows else [], start=start_index):
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
