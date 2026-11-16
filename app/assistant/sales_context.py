import re
from datetime import datetime

from app.assistant.intent_router import (
    BASKET_PER_FAMILY,
    IGNORE_STOCK,
    IntentRouter,
    asks_for_sales_growth,
    basket_family_filters,
)
from app.assistant.tools.schemas import AssistantToolName
from app.db.repositories.conversations import ConversationMessage
from app.modules.procurement.normalization import normalize_text

CONTEXT_TOOLS = {AssistantToolName.SALES_TOP_PRODUCTS, AssistantToolName.SALES_BASKET_ASSOCIATIONS}
REPEAT_REQUEST = re.compile(
    r"^(?:(?:hace|haceme|haga|hacer|repeti|repetir|mostrame|dame)\s+)?(?:lo mismo|igual)\b"
)


def sales_scope(tool: AssistantToolName, arguments: dict, data: dict) -> dict:
    if tool not in CONTEXT_TOOLS or not data.get("date_from") or not data.get("date_to"):
        return {}
    scope = {key: value for key, value in arguments.items() if key != "business_id"}
    scope.update(date_from=data["date_from"], date_to=data["date_to"])
    if tool == AssistantToolName.SALES_BASKET_ASSOCIATIONS:
        scope.update(family_a=data["family_a"], family_b=data["family_b"], level=data["level"])
        scope.update(
            include_families=data.get("include_families", []),
            exclude_families=data.get("exclude_families", []),
            per_family=data.get("per_family", False),
        )
        if "limit" in data:
            scope["limit"] = data["limit"]
    else:
        scope["limit"] = data.get("limit", scope.get("limit", 10))
        if comparison := data.get("comparison"):
            scope["compare"] = True
            if not data.get("growth"):
                scope.update(
                    comparison_date_from=comparison["date_from"],
                    comparison_date_to=comparison["date_to"],
                )
    return {"tool": tool, "arguments": scope}


def planner_context(messages: list[ConversationMessage]) -> dict:
    scope = {}
    for message in reversed(messages):
        candidate = message.payload.get("metadata", {}).get("sales_scope")
        if candidate and candidate.get("tool") in CONTEXT_TOOLS:
            scope = candidate
            break
    return {
        "sales_scope": scope,
        "recent_messages": [
            {"role": "user" if message.direction == "inbound" else "assistant",
             "content": message.text[:1500]}
            for message in messages[-8:] if message.text
        ],
    }


def sales_follow_up(text: str, timestamp: datetime, scope: dict):
    normalized = normalize_text(text).strip(" ¿?¡!.,")
    repeat = REPEAT_REQUEST.match(normalized)
    if not scope:
        if repeat:
            return "¿Qué consulta querés repetir? Indicame el período y los filtros."
        return None
    tool = AssistantToolName(scope["tool"])
    growth = asks_for_sales_growth(normalized)
    if growth and tool != AssistantToolName.SALES_TOP_PRODUCTS:
        return None
    if normalized in {"lo mismo", "igual", "repeti la consulta"}:
        return tool, scope["arguments"]
    refinement = re.match(
        r"^(?:y |ahora |lo mismo(?: pero)?[,:]? |igual(?: pero)?[,:]? |"
        r"(?:solo|unicamente) |(?:comparalo|comparamelo|comparame eso|comparalos|compara eso)\b|"
        r"(?:inclui|exclui|incluyendo|sin contar|tambien) |con |sin |solamente |"
        r"(?:dame|mostrame|traeme) \d+ |(?:los |las )?\d+ mas\b|(?:del|desde el) \d+ al \d+\b)",
        normalized,
    )
    basket_filters = (
        basket_family_filters(text, follow_up=True)
        if tool == AssistantToolName.SALES_BASKET_ASSOCIATIONS and not re.match(
            r"^(?:(?:dame|mostrame|sugerime)\s+)?(?:combos|posibles promociones|promociones)\b",
            normalized,
        ) else {}
    )
    basket_grouping = (
        tool == AssistantToolName.SALES_BASKET_ASSOCIATIONS and BASKET_PER_FAMILY.search(normalized)
    )
    if not (refinement or repeat or growth or basket_filters or basket_grouping):
        return None
    if re.search(
        r"\b(?:stock|precio|costos?|recordatorios?|facture|resumen|ventas totales)\b|"
        r"\bcompar\w* (?:las )?ventas\b",
        IGNORE_STOCK.sub("", normalized),
    ):
        return None
    arguments = dict(scope["arguments"])
    dates = IntentRouter._extract_period(text, timestamp)
    if dates.pop("invalid_dates", False):
        return "¿Qué fechas querés usar para continuar la consulta?"
    if tool == AssistantToolName.SALES_BASKET_ASSOCIATIONS:
        parsed = IntentRouter.extract_basket_query(text, normalized, timestamp, follow_up=True)
        if question := parsed.get("clarification_question"):
            return question
        changes = parsed["arguments"]
        if changes.get("level") == "family":
            changes["per_family"] = False
        if "level" in changes and "family_a" not in changes:
            changes.update(family_a=None, family_b=None)
        elif changes.get("family_a"):
            changes["include_families"] = []
        elif re.search(r"\b(?:todas las familias|sin filtrar por familia)\b", normalized):
            changes.update(
                family_a=None, family_b=None, level="product",
                include_families=[], exclude_families=[],
            )
    else:
        parsed = IntentRouter._extract_sales_top_products_entities(
            text, normalized, reference=timestamp
        )
        changes = dict(dates)
        if growth:
            growth_args = IntentRouter.extract_sales_growth(text, normalized, timestamp)
            changes.update(
                growth=True, compare=True, sort="quantity", show_stock=False,
                rank_from=None, rank_to=None,
            )
            if "baseline_weeks" in growth_args:
                changes.update(
                    baseline_weeks=growth_args["baseline_weeks"],
                    comparison_date_from=None,
                    comparison_date_to=None,
                )
            elif re.search(r"\bsemana (?:anterior|pasada)\b", normalized):
                changes.update(
                    baseline_weeks=1, comparison_date_from=None, comparison_date_to=None,
                )
        if "show_stock" in parsed:
            changes["show_stock"] = False
        if "include_irrelevant_products" in parsed:
            changes["include_irrelevant_products"] = True
        if re.search(
            r"(?:sin|exclui\w*|omiti\w*) (?:contar )?(?:los )?(?:productos )?"
            r"(?:poco relevantes|de baja (?:importancia|relevancia))",
            normalized,
        ):
            changes["include_irrelevant_products"] = False
        if re.search(r"\b(?:todas las familias|sin filtrar por familia)\b", normalized):
            changes.update(family=None, families=[], category=None)
        elif parsed.get("family") or parsed.get("families"):
            changes.update(
                family=parsed.get("family"), families=parsed.get("families", []), category=None,
            )
        elif family := re.fullmatch(r"(?:y )?(?:ahora )?solo de ([a-z ]+)", normalized):
            changes.update(family=family[1], families=[], category=None)
        for key in (
            "exclude_product_names", "exclude_families", "rank_from", "rank_to", "supplier"
        ):
            if parsed.get(key):
                changes[key] = parsed[key]
        limit_match = IntentRouter._extract_ranking_limit(normalized)
        if limit_match or changes.get("rank_to") is not None:
            changes["limit"] = parsed["limit"]
            if not parsed.get("rank_from") and not parsed.get("rank_to"):
                changes.update(rank_from=None, rank_to=None)
        elif more := re.search(r"\b(?:dame |mostrame |traeme )?(\d+)(?:\s+mas)\b", normalized):
            changes.update(
                rank_from=arguments.get("rank_from", 1) or 1,
                rank_to=None,
                limit=int(more[1]),
            )
            changes["rank_from"] += arguments.get("limit", 10)
        if re.search(r"\b(?:margen|facturacion|plata|cantidad|unidades)\b", normalized):
            changes["sort"] = parsed["sort"]
        if re.search(r"\bcompar\w*|\b(?:vs|versus)\b", normalized):
            changes["compare"] = True
            if (
                "comparison_date_from" not in dates and "date_from" in dates
                and not (growth and re.search(r"\bsemana (?:anterior|pasada)\b", normalized))
                and not re.search(r"\b(?:esta semana|este mes)\b.*\bcompar", normalized)
            ):
                changes["comparison_date_from"] = changes.pop("date_from")
                changes["comparison_date_to"] = changes.pop("date_to")
        elif re.match(r"^(?:y )?con (?:el|la|del|desde)\b", normalized) and "date_from" in dates:
            changes.update(
                compare=True,
                comparison_date_from=changes.pop("date_from"),
                comparison_date_to=changes.pop("date_to"),
            )
        if re.search(r"\bsin compar\w*", normalized):
            changes.update(compare=False, comparison_date_from=None, comparison_date_to=None)

    period_text = re.split(r"\bcompar\w*|\b(?:vs|versus)\b", normalized, maxsplit=1)[0]
    days = re.search(r"\bultim[oa]s\s+(\d+)\s+dias\b", period_text)
    if days or "ultimo mes" in period_text:
        changes.update(
            days=min(max(int(days[1]), 1), 365) if days else 30,
            date_from=None, date_to=None,
        )
        if tool == AssistantToolName.SALES_TOP_PRODUCTS:
            changes["period"] = "recent"
    if tool == AssistantToolName.SALES_TOP_PRODUCTS and "date_from" in changes:
        if "comparison_date_from" not in changes:
            changes.update(comparison_date_from=None, comparison_date_to=None)
        changes.setdefault("period", "recent")
    if not changes and repeat and normalized[repeat.end():].strip() not in {"", "pero"}:
        return "¿Qué filtro, período o cantidad de productos querés cambiar?"
    if not changes:
        return None
    return tool, {**arguments, **changes}
