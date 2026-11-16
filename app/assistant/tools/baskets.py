from datetime import timedelta
from itertools import groupby

from app.assistant.tools.filters import UnresolvedSalesFilter
from app.assistant.tools.formatting import format_number
from app.assistant.tools.sales import SalesTopProductsTool
from app.assistant.tools.schemas import (
    AssistantToolName,
    AssistantToolResult,
    BasketAssociationsQuery,
    ToolExecutionContext,
)
from app.modules.sales.analytics import basket_associations


def describe_purchase_frequency(base: str, companion: str, together: int, base_count: int) -> str:
    if together == base_count:
        return (
            f"• Cada compra con {base} también llevó {companion} "
            f"({format_number(together)} de {format_number(base_count)} tickets)."
        )
    if together * 3 <= base_count or together * 2 == base_count:
        frequency = f"1 de cada {format_number(round(base_count / together))}"
    else:
        frequency = f"{min(round(10 * together / base_count), 9)} de cada 10"
    return (
        f"• En aproximadamente {frequency} compras con {base} también se compró {companion} "
        f"({format_number(together)} de {format_number(base_count)} tickets)."
    )


class SalesBasketAssociationsTool:
    name = AssistantToolName.SALES_BASKET_ASSOCIATIONS

    def __init__(self, repository, resolver):
        self.repository = repository
        self.resolver = resolver

    def execute(
        self, *, arguments: dict[str, object], context: ToolExecutionContext
    ) -> AssistantToolResult:
        query = BasketAssociationsQuery.model_validate(arguments)
        business_id = context.business_id
        try:
            family_a = self._resolve_family(query.family_a, business_id)
            family_b = self._resolve_family(query.family_b, business_id)
            included = list(dict.fromkeys(
                self._resolve_family(term, business_id) for term in query.include_families
            ))
            excluded = list(dict.fromkeys(
                self._resolve_family(term, business_id) for term in query.exclude_families
            ))
            if set(included) & set(excluded):
                raise UnresolvedSalesFilter(
                    "Una familia aparece incluida y excluida. ¿Cuál de los dos filtros aplico?"
                )
            pair_families = {family for family in (family_a, family_b) if family}
            if pair_families & set(excluded) or (included and pair_families - set(included)):
                raise UnresolvedSalesFilter(
                    "El cruce anterior incluye una familia fuera del filtro. "
                    "¿Entre qué familias querés buscar ahora?"
                )
            if query.level == "family" and (family_a or family_b):
                raise UnresolvedSalesFilter(
                    "¿Querés comparar familias completas o productos dentro de esas familias?"
                )
        except UnresolvedSalesFilter as exc:
            return AssistantToolResult(
                name=self.name, reply=str(exc), metadata={"requires_clarification": True}
            )
        status = self.repository.get_sales_history_status(business_id=business_id)
        end = query.date_to or status.last_sale_date or SalesTopProductsTool._context_date(context)
        start = query.date_from or end - timedelta(days=query.days - 1)
        if not 0 <= (end - start).days < 365:
            raise ValueError("Invalid basket date range")
        rows = self.repository.iter_ticket_products(
            business_id=business_id, date_from=start, date_to=end
        )
        grouped = query.per_family and query.level == "product" and family_b is None
        group_families = []
        if grouped:
            group_families = [family_a] if family_a else (
                included or self.resolver.list_categories(business_id=business_id)
            )
            group_families = [family for family in group_families if family not in excluded]
        total, pairs, groups = basket_associations(
            self._tickets(rows, query.level),
            family_a=None if grouped else family_a,
            family_b=family_b,
            min_tickets=query.min_tickets,
            limit=query.limit,
            include_families=set(included),
            exclude_families=set(excluded),
            group_families=group_families if grouped else None,
        )
        scope = f" entre {family_a} y {family_b}" if family_b else (
            f" dentro de {family_a}" if family_a and not grouped else ""
        )
        label = (
            "Familias que se compran juntas" if query.level == "family"
            else "Productos que se compran juntos"
        )
        if grouped:
            label = f"Combos por familia · hasta {query.limit} por familia"
        reply = [
            f"{label}{scope}",
            f"Del {start:%d/%m/%Y} al {end:%d/%m/%Y}. "
            f"Se analizaron {format_number(total)} tickets.",
        ]
        if included:
            reply.append("Familias incluidas: " + ", ".join(included) + ".")
        if excluded:
            reply.append("Familias excluidas: " + ", ".join(excluded) + ".")
        if grouped and total:
            for family, group in groups.items():
                if not group:
                    continue
                reply.append(f"\n{family} ({len(group)} de {query.limit})")
                reply.extend(self._format_pairs(group, family=family))
            empty = [family for family, group in groups.items() if not group]
            if empty:
                reply.append(
                    f"\nFamilias sin pares con al menos {query.min_tickets} tickets juntos "
                    "y una frecuencia mayor a la esperada: " + ", ".join(empty) + "."
                )
        elif pairs:
            reply.extend(self._format_pairs(pairs))
        if not pairs and total:
            reply.append(
                f"No encontré pares con al menos {query.min_tickets} tickets juntos "
                "que se compraran juntos más seguido de lo esperado."
            )
        if not total:
            reply.append("No hay tickets de compra cargados para ese período.")
        if total and status.last_sale_date and end > status.last_sale_date:
            reply.append(f"Última fecha con ventas cargadas: {status.last_sale_date:%d/%m/%Y}.")
        return AssistantToolResult(
            name=self.name,
            reply="\n".join(reply),
            data={
                "date_from": start.isoformat(), "date_to": end.isoformat(), "level": query.level,
                "family_a": family_a, "family_b": family_b, "ticket_count": total,
                "min_tickets": query.min_tickets, "limit": query.limit, "pairs": pairs,
                "include_families": included, "exclude_families": excluded, "per_family": grouped,
                "groups": [{"family": family, "pairs": group} for family, group in groups.items()],
            },
        )

    @staticmethod
    def _format_pairs(pairs, *, family=None):
        for index, pair in enumerate(pairs, 1):
            left, right = pair["left"]["name"], pair["right"]["name"]
            support = format_number(pair["support_pct"])
            lift = format_number(pair["lift"])
            together = pair["tickets_together"]
            forward = describe_purchase_frequency(left, right, together, pair["left_tickets"])
            reverse = describe_purchase_frequency(right, left, together, pair["right_tickets"])
            if family:
                frequency = forward if pair["left"]["family"] == family else reverse
                yield f"{index}. {left} + {right} · {together} tickets juntos.\n{frequency}"
                continue
            yield (
                f"\n{index}. {left} + {right}\n"
                f"{forward}\n"
                f"{reverse}\n"
                f"En todo el período aparecieron juntos en el {support}% de los tickets, "
                f"{lift} veces lo esperado por azar."
            )

    def _resolve_family(self, term, business_id):
        if term is None:
            return None
        family = self.resolver.resolve_category(business_id=business_id, query=term).category
        if not family:
            raise UnresolvedSalesFilter(f"No pude resolver la familia «{term}». ¿Cuál querés usar?")
        return family

    @staticmethod
    def _tickets(rows, level):
        for _, lines in groupby(rows, key=lambda row: (row[0], row[1])):
            if level == "family":
                yield {family: (family, family) for _, _, _, _, family in lines if family}
            else:
                yield {
                    product_id: (name or product_id, family)
                    for _, _, product_id, name, family in lines
                }
