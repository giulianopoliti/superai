"""Closed-option catalog enrichment. Decisions proposes; this module validates."""

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal

from app.modules.procurement.normalization import normalize_text

VERSION = "catalog-decisions-v7-auto-taxonomy"
TEXT_ATTRIBUTES = ("product_type", "brand", "line", "variant", "packaging")
FALLBACKS = {
    "unknown": "No hay evidencia suficiente para completar este atributo.",
    "none": "Hay evidencia, pero ninguna opción propuesta corresponde.",
    "not_applicable": "El atributo no aplica a este artículo.",
}
SIZE_RE = re.compile(
    r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*(KGS?|KG|GRS?|GRAMOS?|G|ML|CC|LTS?|LITROS?|L)\b",
    re.IGNORECASE,
)
UNIT_MAP = {
    "g": "g",
    "gr": "g",
    "grs": "g",
    "gramo": "g",
    "gramos": "g",
    "kg": "kg",
    "kgs": "kg",
    "ml": "ml",
    "cc": "ml",
    "l": "l",
    "lt": "l",
    "lts": "l",
    "litro": "l",
    "litros": "l",
}


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


@dataclass
class Option:
    value: str
    label: str
    evidence: str | None
    origin: str
    vocabulary_id: str | None = None
    data: object = None


def snapshot(product) -> dict:
    return {
        "product_id": product.id,
        "business_id": product.business_id,
        "original_name": product.name,
        "external_product_id": product.external_product_id,
        "brand": product.brand,
        "category": product.category,
        "unit_size": str(product.unit_size) if product.unit_size is not None else None,
        "unit": product.unit,
    }


def raw_fragments(name: str) -> list[str]:
    # Propose actual spans only, preserving unfamiliar names for review.
    words = list(re.finditer(r"[^\W\d_]+(?:[.'’][^\W\d_]+)*", name, re.UNICODE))
    spans = []
    for length in (1, 2, 3):
        for index in range(len(words) - length + 1):
            selected = words[index : index + length]
            fragment = name[selected[0].start() : selected[-1].end()]
            if not any(char.isdigit() for char in fragment):
                spans.append(fragment)
    return list(dict.fromkeys(spans))[:40]


def build_options(source: dict, vocabulary: list) -> dict[str, list[Option]]:
    name = source["original_name"]
    normalized = " " + normalize_text(name) + " "
    options = {}
    for attribute in TEXT_ATTRIBUTES:
        candidates = []
        entries = [entry for entry in vocabulary if entry.attribute == attribute]
        for entry in entries:
            hits = [
                alias
                for alias in [entry.label, *entry.aliases]
                if " " + normalize_text(alias) + " " in normalized
            ]
            # Small complete type list avoids a single retrieval failure forcing a new type.
            if hits or attribute == "product_type":
                candidates.append(
                    Option(
                        entry.id,
                        entry.label,
                        hits[0] if hits else name,
                        "vocabulary" if hits else "vocabulary_inferred",
                        entry.id,
                    )
                )
        if attribute == "brand" and source.get("brand"):
            label = source["brand"]
            if not any(normalize_text(c.label) == normalize_text(label) for c in candidates):
                candidates.append(Option("source_brand", label, "brand=" + label, "source"))
        if attribute in ("product_type", "brand", "line", "variant"):
            known = {
                normalize_text(alias)
                for entry in entries
                for alias in [entry.label, *entry.aliases]
            }
            # Do not reoffer a known alias as a new concept, or assign an explicit
            # brand/type/line token to another role. Decisions still evaluates each field.
            occupied_roles = {
                "product_type": {"brand", "line", "variant", "packaging"},
                "brand": {"product_type", "line", "variant", "packaging"},
                "line": {"product_type", "brand", "variant", "packaging"},
                "variant": {"product_type", "brand", "line", "packaging"},
            }[attribute]
            occupied = {
                normalize_text(alias)
                for entry in vocabulary
                if entry.attribute in occupied_roles
                for alias in [entry.label, *entry.aliases]
                if " " + normalize_text(alias) + " " in normalized
            }
            for index, fragment in enumerate(raw_fragments(name)):
                normalized_fragment = normalize_text(fragment)
                if attribute == "product_type" and any(
                    " " + normalized_fragment + " " in " " + alias + " " for alias in known
                ):
                    continue
                if normalized_fragment not in known and not any(
                    normalized_fragment == token
                    or " " + token + " " in " " + normalized_fragment + " "
                    for token in occupied
                ):
                    candidates.append(
                        Option(
                            f"span_{index}",
                            fragment,
                            fragment,
                            "new_fragment",
                        )
                    )
        options[attribute] = candidates

    sizes = []
    for index, match in enumerate(SIZE_RE.finditer(name)):
        value = Decimal(match.group(1).replace(",", "."))
        if value > 0:
            unit = UNIT_MAP[match.group(2).lower()]
            sizes.append(
                Option(
                    f"size_{index}",
                    f"{value} {unit}",
                    match.group(),
                    "name",
                    data={"value": str(value), "unit": unit},
                )
            )
    if source.get("unit_size") and source.get("unit", "") in UNIT_MAP:
        value = Decimal(source["unit_size"])
        unit = UNIT_MAP[source["unit"]]
        if value > 0 and not any(
            Decimal(c.data["value"]) == value and c.data["unit"] == unit for c in sizes
        ):
            sizes.append(
                Option(
                    "source_size",
                    f"{value} {unit}",
                    f"unit_size={value}; unit={unit}",
                    "source",
                    data={"value": str(value), "unit": unit},
                )
            )
    options["content"] = sizes
    options["package_quantity"] = []
    options["minimum_order_quantity"] = []
    patterns = {
        "package_quantity": r"\b(?:PACK|BULTO|CAJA)\s*(?:DE\s*|X\s*)?(\d+)\s*(?:U|UN|UNIDADES)?\b",
        "minimum_order_quantity": r"\b(?:COMPRA\s+MINIMA|MINIMO)\s*[:=]?\s*(\d+)\b",
    }
    for attribute, pattern in patterns.items():
        for index, match in enumerate(re.finditer(pattern, name, re.IGNORECASE)):
            if int(match.group(1)) > 0:
                options[attribute].append(
                    Option(
                        f"count_{index}",
                        match.group(1),
                        match.group(),
                        "name",
                        data=int(match.group(1)),
                    )
                )
    return options


def build_questions(options: dict[str, list[Option]]) -> list[dict]:
    descriptions = {
        "category": "¿Cuál es el tipo general? Elegí la categoría existente equivalente.",
        "subcategory": (
            "¿Qué subtipo explícito corresponde dentro de la categoría indicada? "
            "Dulce, salada, sin sal y suave son variantes, no subtipos. "
            "Si el nombre describe un subtipo que falta en las opciones, elegí none; "
            "si no indica subtipo, elegí unknown."
        ),
        "product_type": "¿Qué tipo de producto es? Preferí un tipo existente equivalente.",
        "brand": "¿Cuál es la marca o nombre comercial? No selecciones tipo ni fabricante.",
        "line": "¿Qué línea explícita tiene? No repitas marca, tipo ni variante.",
        "variant": "¿Qué variante explícita tiene? Puede ser sabor, suavidad o composición.",
        "packaging": "¿Qué tipo de envase está explícito?",
        "content": "¿Cuál es el contenido de UN envase? No selecciones contenido total del pack.",
        "package_quantity": "¿Cuántos envases tiene el pack explícito? No es compra mínima.",
        "minimum_order_quantity": "¿Qué compra mínima está explícita? No es tamaño ni pack.",
    }
    return [
        {
            "type": "choice",
            "name": attribute,
            "instructions": descriptions[attribute],
            "choices": [
                {
                    "value": c.value,
                    "description": c.label
                    + (
                        " (fragmento nuevo; solo si describe este atributo)"
                        if c.origin == "new_fragment"
                        else ""
                    ),
                }
                for c in candidates
            ]
            + [{"value": value, "description": text} for value, text in FALLBACKS.items()],
        }
        for attribute, candidates in options.items()
    ]


def validate_answers(
    response, options: dict, *, threshold: float, source: dict | None = None
) -> dict:
    answers = {answer.name: answer for answer in response.answers}
    if len(answers) != len(response.answers) or set(answers) != set(options):
        raise ValueError("Missing, duplicate or unexpected Decisions answers")
    result = {}
    for attribute, candidates in options.items():
        answer = answers[attribute]
        if answer.type == "refusal":
            result[attribute] = {"status": "refused", "value": None}
            continue
        valid = {c.value for c in candidates} | set(FALLBACKS)
        probabilities = {p.value: p.probability for p in answer.probabilities}
        if (
            answer.type != "choice"
            or answer.choice not in valid
            or len(probabilities) != len(answer.probabilities)
            or set(probabilities) != valid
            or abs(sum(probabilities.values()) - 1) > 0.01
            or probabilities[answer.choice] + 1e-6 < max(probabilities.values())
        ):
            raise ValueError(f"Invalid closed-option response for {attribute}")
        probability = probabilities[answer.choice]
        state = {
            "choice": answer.choice,
            "probability": probability,
            "confidence": answer.confidence,
            "value": None,
            "evidence": None,
        }
        if answer.choice in FALLBACKS:
            state["status"] = answer.choice
        else:
            candidate = next(c for c in candidates if c.value == answer.choice)
            state.update(
                {
                    "proposed_value": candidate.label,
                    "evidence": candidate.evidence,
                    "vocabulary_id": candidate.vocabulary_id,
                    "origin": candidate.origin,
                }
            )
            if probability < threshold:
                state["status"] = "low_probability"
            elif candidate.origin in {"new_fragment", "pending_vocabulary"}:
                state["status"] = "proposed_new"
            elif not candidate.evidence and attribute != "product_type":
                state["status"] = "unsupported"
            else:
                state["status"] = "interpreted"
                state["value"] = candidate.data if candidate.data is not None else candidate.label
        result[attribute] = state
    # Independent questions can choose the same span in incompatible roles.
    for attribute in ("line", "variant"):
        value = result.get(attribute, {}).get("value")
        if value and any(
            normalize_text(value) == normalize_text(result.get(other, {}).get("value") or "")
            for other in ("product_type", "brand", "line")
            if other != attribute
        ):
            result[attribute].update(status="conflict", value=None)
    if source and result.get("content", {}).get("value"):
        name = source["original_name"]
        outer_container = re.search(r"\b(?:CAJA|PACK|BULTO)\b", name, re.IGNORECASE)
        inner_count = re.search(r"\bX?\s*\d+\s*(?:U|UN|UNIDADES)\b", name, re.IGNORECASE)
        if outer_container and inner_count:
            # The observed weight may belong to the entire box, not an inner item.
            result["content"].update(status="ambiguous_container", value=None)
    return result


def interpretation_columns(attributes: dict, original_name: str) -> dict:
    values = {key: row.get("value") for key, row in attributes.items()}
    content = values.pop("content") or {}
    pieces = []
    for key in TEXT_ATTRIBUTES:
        value = values[key]
        if not value:
            continue
        if key == "variant" and (
            " " + normalize_text(value) + " "
            in " " + normalize_text(values["product_type"] or "") + " "
        ):
            continue  # Keep composition structured without repeating it in the display name.
        pieces.append(value)
    if content:
        pieces.append(f"{Decimal(content['value']).normalize():f} {content['unit']}")
    if values["package_quantity"]:
        pieces.append(f"pack x{values['package_quantity']}")
    core_complete = bool(values["product_type"] and values["brand"] and content)
    pending = any(
        row["status"]
        in {
            "needs_review",
            "proposed_new",
            "none",
            "low_probability",
            "unsupported",
            "conflict",
            "refused",
            "ambiguous_container",
            "possible_duplicate",
            "invalid_proposal",
        }
        for row in attributes.values()
    )
    return {
        **values,
        "content_value": Decimal(content["value"]) if content else None,
        "content_unit": content.get("unit"),
        "standard_name": " ".join(pieces) or original_name,
        "status": "needs_review" if pending else ("interpreted" if core_complete else "partial"),
    }
