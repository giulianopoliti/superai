import re
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.modules.procurement.normalization import normalize_text
from app.modules.reminders.due_date_parser import parse_due_at, remove_due_at_expression
from app.schemas.assistant import AssistantRequest
from app.schemas.intents import IntentName, IntentResult

IGNORE_STOCK = re.compile(
    r"\b(?:no (?:te )?(?:guies|bases) (?:por|en)|no (?:uses|mires|consideres|tomes en cuenta)|"
    r"sin (?:usar|mirar|considerar|tomar en cuenta|guiarte por)) (?:el )?stock\b"
)

BASKET_PER_FAMILY = re.compile(
    r"\b(?:(?P<count>\d+|uno|una|un|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)"
    r"\s+(?:(?:combos?|pares?|asociaciones?|promociones?|ideas?)\s+)?)?"
    r"(?:de|para|por)\s+cada\s+familia\b"
)


def asks_for_sales_growth(text: str) -> bool:
    return bool(re.search(
        r"\b(?:vendieron mucho mas|vendieron mas (?:que|comparad\w*|respecto)|"
        r"(?:mayor(?:es)?|mas) (?:suba|aumento|crecimiento)s?|"
        r"(?:subieron|crecieron|aumentaron) (?:mucho )?mas|subas? de ventas|"
        r"crecimiento (?:en|de) ventas)\b",
        text,
    ))


def basket_family_filters(text: str, *, follow_up: bool = False) -> dict[str, list[str]]:
    """Read inclusion and exclusion lists while preserving sentence and list boundaries."""
    filters: dict[str, list[str]] = {}
    exclusion = (
        r"(?:que no (?:sean|vengan) de|sin contar|sin|exclui(?:r|me|yendo)?|excepto)"
    )
    inclusion = r"(?:solo|solamente|unicamente|inclui(?:r|me|yendo)?)"
    remaining = r"(?:(?:y|pero)\s+)?(?:(?:de|con)\s+)?(?:lo demas|el resto)"
    remaining_clause = re.compile(rf"(?:^|,|\s)\s*{remaining}(?:\s+por favor)?\s*$")
    for phrase in re.split(r"[.!?;\n]+", text):
        phrase = normalize_text(phrase.replace(",", " comma_separator ")).replace(
            "comma separator", ","
        )
        if not phrase:
            continue
        if match := remaining_clause.search(phrase):
            phrase = phrase[:match.start()].rstrip(" ,")
            filters.setdefault("include_families", [])
            if not phrase:
                continue
        found = re.search(
            rf"\b{exclusion}\s+(?:(?:la|las)\s+)?familias?\s+(.+?)"
            rf"(?=\s*(?:,\s*)?{inclusion}\b|\s+(?:del \d|desde|durante|por familia)\b|$)",
            phrase,
        )
        if found:
            filters["exclude_families"] = IntentRouter._split_family_terms(
                re.sub(r"\s+o\s+", " y ", found[1])
            )
            phrase = phrase[:found.start()] + " " + phrase[found.end():]
        found = re.search(
            rf"\b{inclusion}\s+(?:de\s+)?(?:(?:la|las)\s+)?familias?\s+(.+)", phrase
        )
        if found:
            family_list = re.split(r"\s+(?:del \d|desde\b|durante\b|por familia\b)", found[1])[0]
        elif (follow_up or filters) and re.fullmatch(
            r"(?:y |ahora )?[a-z][a-z0-9 ]*(?:\s+o\s+|,)[a-z][a-z0-9 ,]*", phrase.strip()
        ):
            family_list = re.sub(r"^(?:y |ahora )+", "", phrase.strip())
        else:
            continue
        filters["include_families"] = IntentRouter._split_family_terms(
            re.sub(r"\s+o\s+", " y ", family_list)
        )
    return filters


class IntentRouter:
    """Rule-based router used as a deterministic fallback for LLM providers."""

    def route(self, request: AssistantRequest) -> IntentResult:
        text = (request.text or "").strip()
        normalized = normalize_text(text)

        if self._looks_like_create_reminder(normalized):
            title = self._extract_reminder_title(text)
            due_at = parse_due_at(text, reference=request.timestamp)
            return IntentResult(
                intent=IntentName.CREATE_REMINDER,
                entities={
                    "title": remove_due_at_expression(title),
                    "due_at": due_at.isoformat() if due_at else None,
                },
                confidence=0.82,
            )

        if self._looks_like_mark_done(normalized):
            return IntentResult(
                intent=IntentName.MARK_REMINDER_DONE,
                entities=self._extract_mark_done_entities(text),
                confidence=0.78,
            )

        if self._looks_like_list_reminders(normalized):
            return IntentResult(intent=IntentName.LIST_REMINDERS, confidence=0.9)

        if self._looks_like_daily_summary(normalized):
            return IntentResult(intent=IntentName.GET_DAILY_SUMMARY, confidence=0.86)

        if self._looks_like_operational_alerts(normalized):
            return IntentResult(intent=IntentName.LIST_OPERATIONAL_ALERTS, confidence=0.85)

        if self._looks_like_operations_dashboard(normalized):
            return IntentResult(intent=IntentName.GET_OPERATIONS_DASHBOARD, confidence=0.84)

        if asks_for_sales_growth(normalized):
            return IntentResult(
                intent=IntentName.QUERY_SALES_TOP_PRODUCTS,
                entities=self.extract_sales_growth(text, normalized, request.timestamp),
                confidence=0.84,
            )
        if IGNORE_STOCK.search(normalized) and re.search(
            r"\b(?:pedir|reponer|reposicion|comprar)\b", normalized
        ) and not self._looks_like_sales_top_products(normalized):
            return IntentResult(
                intent=IntentName.QUERY_TOOL,
                confidence=0.84,
                requires_clarification=True,
                clarification_question=(
                    "Sin stock puedo priorizar por subas de ventas. "
                    "¿Querés ver qué productos subieron esta semana?"
                ),
            )

        common_query = (
            self.extract_basket_query(text, normalized, request.timestamp)
            if re.search(
                r"^promociones$|promociones? (?:posibles|para armar|puedo armar)|"
                r"posibles promociones|"
                r"\b(?:combos?|afinidad|correlaciones?)\b|"
                r"\b(?:productos?|familias?) (?:que |comprados? |compradas? )?"
                r"(?:van |se (?:compran|venden) |salen |aparecen )?junt[oa]s\b|"
                r"\bproductos? de .+? (?:se compran|se venden|van) juntos\b|"
                r"\b(?:sugerime|sugeri|dame|ideas? de)\s+(?:posibles\s+)?promociones\b",
                normalized,
            )
            and not re.match(r"^(?:precio|stock|costo) de\b", normalized)
            else self._extract_common_query(text, normalized, reference=request.timestamp)
        )
        if common_query is not None:
            return IntentResult(
                intent=IntentName.QUERY_TOOL,
                entities=common_query,
                confidence=0.84,
                requires_clarification=bool(common_query.get("clarification_question")),
                clarification_question=common_query.get("clarification_question"),
            )

        if self._looks_like_sales_top_products(normalized):
            return IntentResult(
                intent=IntentName.QUERY_SALES_TOP_PRODUCTS,
                entities=self._extract_sales_top_products_entities(
                    text,
                    normalized,
                    reference=request.timestamp,
                ),
                confidence=0.84,
            )

        return IntentResult(intent=IntentName.UNKNOWN, confidence=0.2)

    @staticmethod
    def _looks_like_create_reminder(normalized: str) -> bool:
        triggers = (
            "recordame",
            "recuerdame",
            "haceme acordar",
            "hacerme acordar",
            "crear recordatorio",
            "agendame",
            "reminder",
        )
        return any(re.match(rf"^(?:por favor\s+)?{trigger}\b", normalized) for trigger in triggers)

    @staticmethod
    def _looks_like_list_reminders(normalized: str) -> bool:
        list_words = ("listar", "lista", "ver", "mostrar", "que tengo")
        reminder_words = ("recordatorio", "recordatorios", "pendiente", "pendientes")
        return any(word in normalized for word in list_words) and any(
            word in normalized for word in reminder_words
        )

    @staticmethod
    def _looks_like_mark_done(normalized: str) -> bool:
        done_words = ("hecho", "listo", "completado", "terminado", "marcar")
        reminder_words = ("recordatorio", "recordame", "tarea", "pendiente")
        return any(word in normalized for word in done_words) and any(
            word in normalized for word in reminder_words
        )

    @staticmethod
    def _looks_like_daily_summary(normalized: str) -> bool:
        return "resumen diario" in normalized or "resumen del dia" in normalized

    @staticmethod
    def _looks_like_operational_alerts(normalized: str) -> bool:
        alert_words = ("alerta", "alertas", "avisos", "problemas")
        operation_words = ("operacion", "operativo", "negocio", "stock", "ventas")
        return any(word in normalized for word in alert_words) and (
            any(word in normalized for word in operation_words) or len(normalized.split()) <= 4
        )

    @staticmethod
    def _looks_like_operations_dashboard(normalized: str) -> bool:
        triggers = (
            "panel general",
            "dashboard",
            "estado general",
            "como viene el negocio",
            "como esta el negocio",
            "resumen operativo",
        )
        return any(trigger in normalized for trigger in triggers)

    @staticmethod
    def _looks_like_sales_top_products(normalized: str) -> bool:
        product_words = ("producto", "productos", "articulo", "articulos")
        sales_words = ("vendido", "vendidos", "ventas", "facturacion", "margen")
        ranking_words = ("mas", "top", "ranking", "mejores")
        return bool(re.search(
            r"\b(?:mas (?:se )?vend(?:i|imos|io)|mas vendidos|top\s*\d*\s+(?:ventas|vendidos))\b",
            normalized,
        )) or (
            any(word in normalized for word in product_words)
            and any(word in normalized for word in sales_words)
            and any(word in normalized for word in ranking_words)
        )

    @staticmethod
    def _extract_sales_top_products_entities(
        text: str,
        normalized: str,
        *,
        reference: datetime,
    ) -> dict[str, object]:
        entities: dict[str, object] = {
            "days": 7,
            "limit": 10,
            "sort": "quantity",
            "exclude_product_names": [],
        }
        if re.search(
            r"sin exclusiones|(?:inclui|incluya|incluyendo|mostra|mostrar|mostrame|tambien)"
            r".*(?:poco relevantes|de baja (?:importancia|relevancia))", normalized,
        ):
            entities["include_irrelevant_products"] = True
        if IntentRouter._looks_like_all_time(normalized):
            entities["period"] = "all_time"
            entities.pop("days", None)

        rank_range = IntentRouter._extract_rank_range(normalized)
        if rank_range is not None:
            rank_from, rank_to = rank_range
            entities["rank_from"] = rank_from
            entities["rank_to"] = rank_to
            entities["limit"] = rank_to - rank_from + 1

        skipped_ranks = IntentRouter._extract_skipped_ranks(normalized)
        if skipped_ranks is not None and rank_range is None:
            entities["rank_from"] = skipped_ranks + 1

        days_match = re.search(r"(?:ultimos|ultimas)\s+(\d+)\s+dias", normalized)
        if days_match:
            entities["days"] = min(max(int(days_match.group(1)), 1), 365)
        elif "ultimo mes" in normalized:
            entities["days"] = 30
        elif "esta semana" in normalized:
            entities["days"] = 7
        elif "ayer" in normalized:
            entities["days"] = 1

        date_from = IntentRouter._extract_sales_date_from(normalized, reference=reference)
        if date_from is not None:
            entities["date_from"] = date_from
            entities.pop("days", None)

        limit_match = IntentRouter._extract_ranking_limit(normalized)
        if limit_match and "rank_to" not in entities:
            entities["limit"] = int(limit_match[1])

        if "margen" in normalized:
            entities["sort"] = "margin"
        elif "facturacion" in normalized or "plata" in normalized:
            entities["sort"] = "revenue"

        filter_text = re.sub(
            r"(?:inclu[ií]|incluya|incluyendo|mostr[aá]|mostrar|mostrame|"
            r"sin contar|sin|exclu[ií]|excluyendo|omit[ií])\s+"
            r"(?:tambi[eé]n\s+)?(?:los\s+)?(?:productos\s+)?"
            r"(?:poco relevantes|de baja (?:importancia|relevancia))|sin exclusiones",
            "", text, flags=re.IGNORECASE,
        )
        filters = IntentRouter._extract_sales_filters(filter_text)
        entities.update(filters)
        if IGNORE_STOCK.search(normalized):
            entities["show_stock"] = False
        entities.update(IntentRouter._extract_period(text, reference))
        if IntentRouter._looks_like_sales_top_products(normalized) and re.search(
            r"\bcompar\w*|\b(?:vs|versus)\b", normalized
        ):
            entities["compare"] = True
        return entities

    @staticmethod
    def extract_sales_growth(text: str, normalized: str, reference: datetime) -> dict:
        entities = IntentRouter._extract_sales_top_products_entities(
            text, normalized, reference=reference,
        )
        entities.update(growth=True, compare=True, sort="quantity", show_stock=False)
        weeks = re.search(r"\b(\d+)\s+semanas? (?:anteriores?|pasadas?)\b", normalized)
        if weeks:
            entities["baseline_weeks"] = int(weeks[1])
        elif re.search(r"\bsemanas (?:anteriores|pasadas)\b", normalized):
            entities["baseline_weeks"] = 4
        return entities

    @staticmethod
    def extract_basket_query(
        text: str, normalized: str, reference: datetime, *, follow_up: bool = False,
    ) -> dict[str, object]:
        arguments = IntentRouter._extract_period(text, reference)
        if arguments.pop("invalid_dates", False) or "comparison_date_from" in arguments:
            return {"clarification_question": "¿Qué período querés usar para buscar los pares?"}
        days = re.search(r"(?:ultimos|ultimas)\s+(\d+)\s+dias", normalized)
        if days:
            arguments["days"] = min(max(int(days[1]), 1), 365)
        limit = re.search(r"\b(\d+)\s+(?:combos|pares|asociaciones|promociones)\b", normalized)
        if limit:
            arguments["limit"] = min(max(int(limit[1]), 1), 20)
        grouping = BASKET_PER_FAMILY.search(normalized)
        if grouping:
            arguments.update(per_family=True, level="product", family_a=None, family_b=None)
            if count := grouping["count"]:
                words = (
                    "un", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez"
                )
                value = int(count) if count.isdecimal() else words.index(
                    "un" if count in {"uno", "una"} else count
                ) + 1
                arguments["limit"] = min(max(value, 1), 20)
            normalized = normalized[:grouping.start()] + " " + normalized[grouping.end():]
        arguments.update(basket_family_filters(text, follow_up=follow_up))
        if grouping and "include_families" not in arguments:
            listed = re.search(
                rf"{BASKET_PER_FAMILY.pattern}\s*:\s*(?P<families>[^\n.!?;]+)",
                text.lower(),
            )
            if listed:
                terms = listed["families"].split(",")
                arguments["include_families"] = IntentRouter._split_family_terms(
                    re.sub(r"\s+o\s+", " y ", ", ".join(normalize_text(term) for term in terms))
                )
        if "include_families" in arguments:
            arguments.update(family_a=None, family_b=None)
            if not grouping and re.search(
                r"\b(?:por familia|familias se (?:compran|venden))", normalized
            ):
                arguments["level"] = "family"
            return {"tool_name": "sales.basket_associations", "arguments": arguments}
        scope = re.search(
            r"\b(?:entre|cruza|cruzar|dentro de|solo(?: de)?|de la|de las|en la|en las|"
            r"(?:de|en)(?=\s+familias?\b))\s+"
            r"(?:(?:la|las)\s+)?(?:familias?\s+)?(?P<families>.+)",
            normalized,
        )
        if not scope:
            scope = re.search(
                r"\b(?:(?:combos|productos juntos|pares)\s+(?:de|con)\s+(?!los ultimos\b)|"
                r"productos? de (?=.+? (?:se compran|se venden|van) juntos\b))"
                r"(?P<families>.+?)(?=\s+(?:se compran|se venden|van) juntos\b|$)",
                normalized,
            )
        if not scope and follow_up:
            scope = re.match(
                r"^(?:y )?(?:ahora )?(?:lo mismo )?con (?P<families>[a-z][a-z ]*)$",
                normalized,
            )
            if scope:
                arguments["per_family"] = True
        terms = []
        if scope:
            tail = re.split(
                r"\s+(?:(?:del|el)\s+\d|desde\b|hasta\b|durante\b|con estadisticas\b|"
                r"por (?:familia|producto)s?\b|en (?:los ultimos|la semana|el mes)\b)",
                scope["families"],
                maxsplit=1,
            )[0]
            tail = re.split(
                r"\s+(?:sin|excepto|exclui\w*)\s+(?:la |las )?familias?\s+|"
                r"\s+por ejemplo\b",
                tail,
            )[0]
            tail = re.sub(r"\s+con\s+", " y ", tail)
            terms = IntentRouter._split_family_terms(tail)
            terms = [
                re.sub(r"^(?:de )?(?:la |las )?(?:familias?\s+)", "", term).strip()
                for term in terms
            ]
        if len(terms) > 2:
            return {"clarification_question": "¿Qué una o dos familias querés cruzar?"}
        if terms and terms[0] not in {"familia", "familias", "productos", "producto", "todas"}:
            arguments["family_a"] = terms[0]
            arguments["family_b"] = terms[1] if len(terms) == 2 else None
            arguments["level"] = "product"
            if len(terms) == 2 or re.search(r"\bdentro de\b", normalized):
                arguments["per_family"] = False
            elif re.search(r"\b(?:combos|productos juntos|pares) con\b", normalized):
                arguments["per_family"] = True
        elif re.search(
            r"\b(?:por familia|entre familias|familias juntas|familias se (?:compran|venden))",
            normalized,
        ):
            arguments["level"] = "family"
        return {"tool_name": "sales.basket_associations", "arguments": arguments}

    @staticmethod
    def _extract_sales_family(normalized: str) -> str | None:
        families = IntentRouter._extract_sales_families(normalized)
        return families[0] if families else None

    @staticmethod
    def _extract_sales_families(normalized: str) -> list[str]:
        clause_boundary = (
            r"\s*,?\s+(?:y\s+)?(?:si\b|con\s+(?:el\s+)?(?:join|proveedor)\b|"
            r"del\s+proveedor\b|proveedor\b)"
        )
        explicit_patterns = [
            (
                r"\b(?:familias?|categorias?|rubros?)\s+(.+?)"
                r"(?=\s*,?\s*(?:y|e)?\s*(?:de\s+la\s+|del\s+|de\s+)?"
                r"(?:familia|categoria|rubro)\b|\s+desde\b|\s+hasta\b|"
                r"\s+excluyendo\b|\s+sin contar\b|\s+menos\b|" + clause_boundary + r"|$)"
            ),
        ]
        families: list[str] = []
        for pattern in explicit_patterns:
            for match in re.finditer(pattern, normalized):
                family = IntentRouter._clean_family_candidate(match.group(1))
                if family:
                    families.extend(IntentRouter._split_family_terms(family))

        if not families:
            families.extend(IntentRouter._extract_implicit_sales_families(normalized))

        seen: set[str] = set()
        unique_families: list[str] = []
        for family in families:
            key = family.casefold()
            if key not in seen:
                seen.add(key)
                unique_families.append(family)
        return unique_families

    @staticmethod
    def _extract_implicit_sales_families(normalized: str) -> list[str]:
        patterns = [
            (
                r"\b(?:productos?\s+)?mas\s+vendidos\s+de\s+([a-z0-9 ]+?)"
                r"(?:\s*,|\s+(?:y\s+)?(?:si|con|del proveedor)\b|"
                r"\s+desde\b|\s+hasta\b|\s+excluyendo|\s+sin contar|\s+menos|$)"
            ),
        ]
        for pattern in patterns:
            match = re.search(pattern, normalized)
            if not match:
                continue
            return IntentRouter._split_family_terms(match.group(1))
        return []

    @staticmethod
    def _split_family_terms(value: str) -> list[str]:
        return [
            cleaned
            for term in re.split(r"\s+y\s+|\s+e\s+|,", value)
            if (cleaned := IntentRouter._clean_family_candidate(term))
        ]

    @staticmethod
    def _clean_family_candidate(value: str) -> str | None:
        family = value.strip()
        stop_phrases = (
            "por favor",
            "los ultimos",
            "las ultimas",
            "esta semana",
            "este mes",
            "semana pasada",
            "mes pasado",
            "semana anterior",
            "mes anterior",
            "compar",
            " vs ",
            " versus ",
            " contra ",
            "hoy",
            "ayer",
            "ultimo mes",
            "desde marzo",
            "hasta ahora",
        )
        for stop_phrase in stop_phrases:
            family = family.split(stop_phrase)[0].strip()
        return IntentRouter._clean_entity_text(family) or None

    @staticmethod
    def _extract_sales_date_from(normalized: str, *, reference: datetime) -> str | None:
        month_numbers = {
            "enero": 1,
            "febrero": 2,
            "marzo": 3,
            "abril": 4,
            "mayo": 5,
            "junio": 6,
            "julio": 7,
            "agosto": 8,
            "septiembre": 9,
            "setiembre": 9,
            "octubre": 10,
            "noviembre": 11,
            "diciembre": 12,
        }
        match = re.search(
            r"\bdesde\s+(enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
            r"septiembre|setiembre|octubre|noviembre|diciembre)\b",
            normalized,
        )
        if not match:
            return None

        month = month_numbers[match.group(1)]
        year = reference.year
        if month > reference.month:
            year -= 1
        return f"{year:04d}-{month:02d}-01"

    @staticmethod
    def _looks_like_all_time(normalized: str) -> bool:
        triggers = (
            "historico",
            "historicos",
            "todo el historial",
            "desde siempre",
            "todos los tiempos",
            "total historico",
        )
        return any(trigger in normalized for trigger in triggers)

    @staticmethod
    def _clean_entity_text(value: str) -> str:
        cleaned = value.strip(" .,:;¿?¡!")
        phrases = (
            "por favor",
            "porfa",
            "gracias",
            "dale",
            "si podes",
            "de la categoria",
            "categoria",
            "familia",
        )
        for phrase in phrases:
            cleaned = re.sub(rf"\b{re.escape(phrase)}\b", "", cleaned).strip()
        return re.sub(r"\s+", " ", cleaned).strip(" .,:;¿?¡!")

    @staticmethod
    def _extract_rank_range(normalized: str) -> tuple[int, int] | None:
        patterns = [
            r"\btop\s+(\d+)\s+(?:al|a|hasta)\s+(?:top\s+)?(\d+)\b",
            r"\b(?:del|desde\s+el)\s+top\s+(\d+)\s+(?:al|a|hasta)\s+(?:top\s+)?(\d+)\b",
            r"\b(?:del|desde\s+el)\s+(\d+)\s+(?:al|a|hasta)\s+(\d+)\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, normalized)
            if not match:
                continue
            rank_from = int(match[1])
            rank_to = int(match[2])
            if rank_from <= rank_to:
                return rank_from, rank_to
        return None

    @staticmethod
    def _extract_skipped_ranks(normalized: str) -> int | None:
        match = re.search(
            r"\b(?:excluyendo|sin contar|menos)\s+(?:los|las)?\s*primer(?:os|as)?\s+(\d+)\b",
            normalized,
        )
        if not match:
            return None
        return int(match[1])

    @staticmethod
    def _extract_limit_match(normalized: str):
        match = re.search(r"\btop\s+(\d+)\b", normalized)
        if match is not None:
            return match
        if re.search(r"\b(?:excluyendo|sin contar|menos)\s+(?:los|las)?\s*primer", normalized):
            return None
        return re.search(r"\b(?:los|las|primeros|primeras)\s+(\d+)\b", normalized)

    @staticmethod
    def _extract_ranking_limit(normalized: str):
        return IntentRouter._extract_limit_match(normalized) or re.search(
            r"\b(\d+)\s+(?:registros|resultados|productos)\b", normalized
        )

    @staticmethod
    def _extract_exclusion_terms(text: str) -> list[str]:
        return IntentRouter._extract_sales_filters(text)["exclude_product_names"]

    @staticmethod
    def _extract_sales_filters(text: str) -> dict[str, object]:
        text = re.split(
            r"[.!?;]\s*(?:lo dem[aá]s|el resto|no te gu[ií]es|alguno\b)",
            text, maxsplit=1, flags=re.IGNORECASE,
        )[0]
        normalized = (
            normalize_text(text.replace(",", " comma_separator "))
            .replace("comma separator", ",")
            .replace("comma_separator", ",")
        )
        clauses = list(
            re.finditer(
                r"\b(?:excluyendo|excluir|sin contar|menos|excepto)\s+(.+?)"
                r"(?=\s+(?:y\s+)?(?:excluyendo|excluir|sin contar|excepto)\b|$)",
                normalized,
            )
        )
        product_terms, excluded_families = [], []
        for match in clauses:
            clause = re.split(r"\b(?:desde|hasta|durante|en los ultimos)\b", match.group(1))[0]
            if re.match(r"\s*(los|las)?\s*primer(os|as)?\s+\d+\s*$", clause):
                continue
            family_match = re.search(r"\b(?:familias?|categorias?|rubros?)\s+(.+)", clause)
            if family_match:
                excluded_families.extend(
                    IntentRouter._split_family_terms(
                        re.sub(r"\s+o\s+", " y ", family_match.group(1))
                    )
                )
                before = clause[: family_match.start()]
                before = re.split(r"\b(?:y|e)\s+(?:cualquiera|todo|todos)\b", before)[0]
                # Only explicitly listed product terms before a family exclusion belong here.
                if not re.search(r"cualquiera|venga|pertenezca|provenga", before):
                    product_terms.extend(re.split(r",|\s+y\s+|\s+e\s+", before))
            else:
                product_terms.extend(re.split(r",|\s+y\s+|\s+e\s+", clause))
        included_text = normalized
        for match in reversed(clauses):
            included_text = included_text[: match.start()] + included_text[match.end() :]
        included_families = IntentRouter._extract_sales_families(included_text)
        result = {
            "exclude_product_names": list(
                dict.fromkeys(
                    cleaned
                    for term in product_terms
                    if (
                        cleaned := IntentRouter._clean_entity_text(
                            re.sub(r"\b(productos?|articulos?)\b", "", term)
                        )
                    )
                )
            ),
            "exclude_families": list(dict.fromkeys(excluded_families)),
        }
        supplier = re.search(
            r"\bproveedor\s+(.+?)(?=\s*,|\s+(?:mejor|dame|si|desde|hasta|"
            r"durante|por favor|excluyendo|sin contar|familias?|categorias?|rubros?)\b|$)",
            normalized,
        )
        if supplier:
            result["supplier"] = IntentRouter._clean_entity_text(supplier[1])
            result["supplier_optional"] = bool(re.search(
                r"\b(?:si no|sino|si tenes|si tienes|si hay|si podes|si puedes)\b", normalized
            ))
        if len(included_families) == 1:
            result["family"] = included_families[0]
        elif included_families:
            result["families"] = included_families
        return result

    @staticmethod
    def _extract_period(normalized: str, reference: datetime) -> dict[str, object]:
        normalized = "".join(
            character for character in unicodedata.normalize("NFKD", normalized.lower())
            if not unicodedata.combining(character)
        )
        today = (
            reference.astimezone(ZoneInfo("America/Buenos_Aires")).date()
            if reference.tzinfo else reference.date()
        )
        if re.search(r"\bcompar\w*|\b(?:vs|versus)\b", normalized):
            clauses = re.split(r"\s+(?:con|contra|vs\.?|versus)\s+", normalized)
            if len(clauses) > 1:
                primary = IntentRouter._extract_period(" ".join(clauses[:-1]), reference)
                comparison = IntentRouter._extract_period(clauses[-1], reference)
                if primary.get("invalid_dates") or comparison.get("invalid_dates"):
                    return {"invalid_dates": True}
                if comparison and "comparison_date_from" not in primary:
                    return {
                        **primary,
                        "comparison_date_from": comparison["date_from"],
                        "comparison_date_to": comparison["date_to"],
                    }
        # Parse explicit dates before calendar words, so "hasta hoy" cannot
        # silently erase an explicitly requested interval.
        date_tokens = re.findall(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}(?:/\d{4})?", normalized)
        if date_tokens:
            if len(date_tokens) not in {1, 2, 4}:
                return {"invalid_dates": True}
            try:
                dates = []
                for token in date_tokens:
                    if "-" in token:
                        dates.append(datetime.strptime(token, "%Y-%m-%d").date())
                    else:
                        parts = token.split("/")
                        dates.append(today.replace(
                            day=int(parts[0]), month=int(parts[1]),
                            year=int(parts[2]) if len(parts) == 3 else today.year,
                        ))
                if len(dates) == 1:
                    if re.search(r"\bdesde\b", normalized):
                        dates.append(today)
                    else:
                        dates.append(dates[0])
                result = {"date_from": dates[0].isoformat(), "date_to": dates[1].isoformat()}
                if len(dates) == 4:
                    result.update(
                        comparison_date_from=dates[2].isoformat(),
                        comparison_date_to=dates[3].isoformat(),
                    )
                return result
            except ValueError:
                return {"invalid_dates": True}

        def relative_period(text):
            if re.search(r"\banteayer\b", text):
                value = today - timedelta(days=2)
                return value, value
            if re.search(r"\bayer\b", text):
                value = today - timedelta(days=1)
                return value, value
            if re.search(r"\bhoy\b", text):
                return today, today
            if re.search(r"semana (?:pasada|anterior)", text):
                end = today - timedelta(days=today.weekday() + 1)
                return end - timedelta(days=6), end
            if re.search(r"mes (?:pasado|anterior)", text):
                end = today.replace(day=1) - timedelta(days=1)
                return end.replace(day=1), end
            weeks_ago = re.search(r"\bhace ([1-9]|1[0-2]) semanas?\b", text)
            if weeks_ago:
                start = today - timedelta(days=today.weekday(), weeks=int(weeks_ago[1]))
                return start, start + timedelta(days=6)
            if "esta semana" in text:
                return today - timedelta(days=today.weekday()), today
            if "este mes" in text:
                return today.replace(day=1), today
            return None

        period = relative_period(normalized)
        return (
            {"date_from": period[0].isoformat(), "date_to": period[1].isoformat()}
            if period else {}
        )

    @staticmethod
    def _extract_common_query(text: str, normalized: str, *, reference: datetime):
        tool = None
        arguments = {}
        # Rankings are more specific than broad phrases such as "ventas de".
        if IntentRouter._looks_like_sales_top_products(normalized):
            return None
        if not IGNORE_STOCK.search(normalized) and any(
            term in normalized for term in ("stock", "existencias", "agotados")
        ):
            tool = "inventory.stock_status"
            threshold = re.search(
                r"(?:hasta|menor(?:es)? o igual(?:es)? a)\s*(\d+(?:[.,]\d+)?)", normalized
            )
            if "stock bajo" in normalized and not threshold:
                return {"clarification_question": "¿Hasta qué cantidad considerás stock bajo?"}
            match = re.search(r"(?:stock|existencias)\s+(?:de|del)\s+(.+)", normalized)
            arguments["query"] = IntentRouter._clean_entity_text(match[1]) if match else ""
            if any(term in normalized for term in ("sin stock", "agotados", "sin existencias")):
                arguments["max_stock"] = 0
            elif threshold:
                arguments["max_stock"] = threshold[1].replace(",", ".")
        elif any(term in normalized for term in (
            "busca", "precio de", "cuanto cuesta", "que precio tiene", "a cuanto vendo", "costo de"
        )):
            tool = "catalog.search_products"
            match = re.search(
                r"(?:busca(?:me|r)?|precio de|cuanto cuesta|que precio tiene|"
                r"a cuanto vendo|costo de)\s+(.+)",
                normalized,
            )
            arguments["query"] = IntentRouter._clean_entity_text(match[1]) if match else ""
        elif "compar" in normalized and any(term in normalized for term in (
            "venta", "factur", "periodo", "semana", "mes", "hoy", "ayer"
        )):
            tool = "sales.compare_periods"
        else:
            match = re.search(
                r"(?:cuanto vend(?:i|imos) de|ventas de|historial de(?: ventas de)?)\s+(.+?)"
                r"(?=\s+(?:en |durante |de los ultimos |los ultimos |desde |del |este mes|"
                r"esta semana|mes pasado|semana pasada|ayer|hoy)|$)", normalized,
            )
            product = IntentRouter._clean_entity_text(match[1]) if match else ""
            temporal_only = bool(re.match(
                r"^(?:hoy|ayer|anteayer|esta semana|este mes|los ultimos|\d{1,2}/|\d{4}-)", product
            ))
            if product and not temporal_only and not any(term in product for term in (
                "familia", "categoria", "semana", "mes"
            )):
                tool = "sales.product_history"
                arguments["product"] = product
            elif re.search(
                r"cuanto vend(?:i|imos)|cuanto factur(?:e|amos)|resumen de ventas|total de ventas|"
                r"ticket promedio|facturacion|margen de ventas|\bventas\b", normalized,
            ):
                tool = "sales.summary"
        if tool is None:
            return None
        if tool.startswith("sales."):
            entities = IntentRouter._extract_sales_top_products_entities(
                text, normalized, reference=reference
            )
            if entities.get("supplier"):
                return {"clarification_question": (
                    "Puedo cruzar proveedor en el ranking de productos más vendidos. "
                    "¿Querés ese ranking?"
                )}
            allowed = {"days", "date_from", "date_to", "invalid_dates"}
            arguments.update({
                key: value for key, value in entities.items()
                if (key in allowed if tool == "sales.product_history" else key not in {
                    "sort", "limit", "rank_from", "rank_to", "include_irrelevant_products"
                })
            })
        else:
            count = IntentRouter._extract_limit_match(normalized)
            arguments["limit"] = min(max(int(count[1]), 1), 50) if count else 5
        return {"tool_name": tool, "arguments": arguments}

    @staticmethod
    def _extract_reminder_title(text: str) -> str:
        title = text.strip()
        patterns = [
            r"(?i)^recordame\s+(que\s+)?",
            r"(?i)^recuerdame\s+(que\s+)?",
            r"(?i)^recu.rdeme\s+(que\s+)?",
            r"(?i)^haceme\s+acordar\s+(que\s+)?",
            r"(?i)^hacerme\s+acordar\s+(que\s+)?",
            r"(?i)^crear\s+recordatorio\s+(para\s+)?",
            r"(?i)^agendame\s+(que\s+)?",
            r"(?i)^reminder\s+",
        ]
        for pattern in patterns:
            title = re.sub(pattern, "", title).strip()
        return title

    @staticmethod
    def _extract_mark_done_entities(text: str) -> dict[str, str]:
        id_match = re.search(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            text,
            flags=re.IGNORECASE,
        )
        if id_match:
            return {"reminder_id": id_match.group(0)}

        title_query = re.sub(
            r"(?i)(marcar|como|hecho|listo|completado|terminado|recordatorio|tarea|pendiente)",
            "",
            text,
        ).strip()
        return {"title_query": title_query}
