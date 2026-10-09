import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.modules.procurement.normalization import normalize_text
from app.modules.reminders.due_date_parser import parse_due_at, remove_due_at_expression
from app.schemas.assistant import AssistantRequest
from app.schemas.intents import IntentName, IntentResult


class IntentRouter:
    """Rule-based router used as a deterministic fallback for LLM providers."""

    def route(self, request: AssistantRequest) -> IntentResult:
        text = (request.text or "").strip()
        normalized = normalize_text(text)

        if self._looks_like_list_reminders(normalized):
            return IntentResult(intent=IntentName.LIST_REMINDERS, confidence=0.9)

        if self._looks_like_daily_summary(normalized):
            return IntentResult(intent=IntentName.GET_DAILY_SUMMARY, confidence=0.86)

        if self._looks_like_operational_alerts(normalized):
            return IntentResult(intent=IntentName.LIST_OPERATIONAL_ALERTS, confidence=0.85)

        if self._looks_like_operations_dashboard(normalized):
            return IntentResult(intent=IntentName.GET_OPERATIONS_DASHBOARD, confidence=0.84)

        common_query = self._extract_common_query(text, normalized, reference=request.timestamp)
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

        if self._looks_like_mark_done(normalized):
            return IntentResult(
                intent=IntentName.MARK_REMINDER_DONE,
                entities=self._extract_mark_done_entities(text),
                confidence=0.78,
            )

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
        return any(trigger in normalized for trigger in triggers)

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
        return (
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

        limit_match = IntentRouter._extract_limit_match(normalized)
        if limit_match and "rank_to" not in entities:
            entities["limit"] = min(max(int(limit_match.group(1)), 1), 50)

        if "margen" in normalized:
            entities["sort"] = "margin"
        elif "facturacion" in normalized or "plata" in normalized:
            entities["sort"] = "revenue"

        filters = IntentRouter._extract_sales_filters(text)
        entities.update(filters)
        entities.update(IntentRouter._extract_period(text.lower(), reference))
        return entities

    @staticmethod
    def _extract_sales_family(normalized: str) -> str | None:
        families = IntentRouter._extract_sales_families(normalized)
        return families[0] if families else None

    @staticmethod
    def _extract_sales_families(normalized: str) -> list[str]:
        explicit_patterns = [
            (
                r"\b(?:familia|categoria|rubro)\s+(.+?)"
                r"(?=\s*,?\s*(?:y|e)?\s*(?:de\s+la\s+|del\s+|de\s+)?"
                r"(?:familia|categoria|rubro)\b|\s+desde\b|\s+hasta\b|"
                r"\s+excluyendo\b|\s+sin contar\b|\s+menos\b|$)"
            ),
        ]
        families: list[str] = []
        for pattern in explicit_patterns:
            for match in re.finditer(pattern, normalized):
                family = IntentRouter._clean_family_candidate(match.group(1))
                if family:
                    families.append(family)

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
                r"\bproductos?\s+mas\s+vendidos\s+de\s+([a-z0-9 ]+?)"
                r"(?:\s+desde\b|\s+hasta\b|\s+excluyendo|\s+sin contar|\s+menos|$)"
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
            "los ultimos",
            "las ultimas",
            "esta semana",
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
        cleaned = value.strip(" .,:;")
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
        return re.sub(r"\s+", " ", cleaned).strip(" .,:;")

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
            rank_from = min(max(int(match.group(1)), 1), 50)
            rank_to = min(max(int(match.group(2)), 1), 50)
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
        return min(max(int(match.group(1)), 1), 49)

    @staticmethod
    def _extract_limit_match(normalized: str):
        match = re.search(r"\btop\s+(\d+)\b", normalized)
        if match is not None:
            return match
        if re.search(r"\b(?:excluyendo|sin contar|menos)\s+(?:los|las)?\s*primer", normalized):
            return None
        return re.search(r"\b(?:los|las|primeros|primeras)\s+(\d+)\b", normalized)

    @staticmethod
    def _extract_exclusion_terms(text: str) -> list[str]:
        return IntentRouter._extract_sales_filters(text)["exclude_product_names"]

    @staticmethod
    def _extract_sales_filters(text: str) -> dict[str, object]:
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
        if len(included_families) == 1:
            result["family"] = included_families[0]
        elif included_families:
            result["families"] = included_families
        return result

    @staticmethod
    def _extract_period(normalized: str, reference: datetime) -> dict[str, object]:
        today = (
            reference.astimezone(ZoneInfo("America/Buenos_Aires")).date()
            if reference.tzinfo
            else reference.date()
        )
        if "ayer" in normalized:
            value = (today - timedelta(days=1)).isoformat()
            return {"date_from": value, "date_to": value}
        if re.search(r"\bhoy\b", normalized):
            return {"date_from": today.isoformat(), "date_to": today.isoformat()}
        if "esta semana" in normalized:
            return {
                "date_from": (today - timedelta(days=today.weekday())).isoformat(),
                "date_to": today.isoformat(),
            }
        if "este mes" in normalized:
            return {"date_from": today.replace(day=1).isoformat(), "date_to": today.isoformat()}
        matches = list(
            re.finditer(
                r"(\d{1,2})/(\d{1,2})(?:/(\d{4}))?"
                r"\s+(?:al|hasta|y)\s+(\d{1,2})/(\d{1,2})(?:/(\d{4}))?",
                normalized,
            )
        )
        if matches:
            try:
                result = {}
                if len(matches) > 2:
                    return {"invalid_dates": True}
                for index, match in enumerate(matches):
                    start = today.replace(
                        day=int(match[1]), month=int(match[2]), year=int(match[3] or today.year)
                    )
                    end = today.replace(
                        day=int(match[4]), month=int(match[5]), year=int(match[6] or today.year)
                    )
                    prefix = "comparison_" if index else ""
                    result[prefix + "date_from"] = start.isoformat()
                    result[prefix + "date_to"] = end.isoformat()
                return result
            except ValueError:
                return {"invalid_dates": True}
        if re.search(r"\d{1,2}/\d{1,2}|\d{4}-\d{2}-\d{2}", normalized):
            return {"invalid_dates": True}
        return {}

    @staticmethod
    def _extract_common_query(text: str, normalized: str, *, reference: datetime):
        tool = None
        arguments = {}
        if any(term in normalized for term in ("stock", "sin existencias", "agotados")):
            tool = "inventory.stock_status"
            if "stock bajo" in normalized:
                return {"clarification_question": "Hasta que cantidad consideras stock bajo?"}
            match = re.search(r"(?:stock|existencias)\s+(?:de|del)\s+(.+)", normalized)
            arguments["query"] = match[1].strip(" .?") if match else ""
            if any(term in normalized for term in ("sin stock", "agotados", "sin existencias")):
                arguments["max_stock"] = 0
        elif any(
            term in normalized
            for term in ("busca", "buscame", "buscar", "precio de", "cuanto cuesta")
        ):
            tool = "catalog.search_products"
            match = re.search(r"(?:busca(?:me|r)?|precio de|cuanto cuesta)\s+(.+)", normalized)
            arguments["query"] = match[1].strip(" .?") if match else ""
        elif "compar" in normalized and any(
            term in normalized for term in ("venta", "facturacion", "periodo", "semana", "mes")
        ):
            tool = "sales.compare_periods"
        elif (
            any(
                term in normalized
                for term in (
                    "cuanto vendi",
                    "cuanto facture",
                    "resumen de ventas",
                    "total de ventas",
                    "ticket promedio",
                    "facturacion total",
                )
            )
            and "cuanto vendi de" not in normalized
        ):
            tool = "sales.summary"
        else:
            match = re.search(
                r"(?:cuanto vendi de|ventas de|historial de(?: ventas de)?)\s+(.+?)"
                r"(?=\s+(?:en |durante |de los ultimos |los ultimos |desde |del |ayer|hoy)|$)",
                normalized,
            )
            if match and not any(
                term in match[1] for term in ("familia", "categoria", "semana", "mes")
            ):
                tool = "sales.product_history"
                arguments["product"] = match[1].strip(" .?")
            elif any(
                term in normalized
                for term in (
                    "cuanto vendi",
                    "cuanto facture",
                    "resumen de ventas",
                    "total de ventas",
                    "ticket promedio",
                    "facturacion total",
                )
            ):
                tool = "sales.summary"
        if tool is None:
            return None
        if tool.startswith("sales."):
            entities = IntentRouter._extract_sales_top_products_entities(
                text, normalized, reference=reference
            )
            if tool == "sales.product_history":
                arguments.update(
                    {
                        key: value
                        for key, value in entities.items()
                        if key in {"days", "date_from", "date_to", "invalid_dates"}
                    }
                )
            else:
                arguments.update(
                    {
                        key: value
                        for key, value in entities.items()
                        if key not in {"sort", "limit", "rank_from", "rank_to"}
                    }
                )
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
