"""Deterministic reader agreement, independent from catalog matching."""

import re
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Literal, Protocol

from pydantic import BaseModel, Field, model_validator

from app.modules.procurement.normalization import (
    UNIT_PATTERN,
    normalize_text,
    parse_package_quantity,
)
from app.modules.procurement.schemas import ExtractedSupplierOfferItem, SupplierOfferObservation


class OfferReconciliationDecision(BaseModel):
    decision: Literal["same_offer", "different_offer", "insufficient"]
    confidence: Decimal = Field(ge=0, le=1)
    selected_source: Literal["gemini", "openai"] | None = None
    compatible_fields: list[
        Literal["brand", "unit_size", "unit", "package_quantity", "tax_included"]
    ]
    conflicting_fields: list[
        Literal[
            "raw_name",
            "brand",
            "unit_size",
            "unit",
            "package_quantity",
            "offer_price",
            "currency",
            "price_type",
            "tax_included",
        ]
    ]
    reason: str

    @model_validator(mode="after")
    def validate_fields(self):
        if set(self.compatible_fields) & set(self.conflicting_fields):
            raise ValueError("A field cannot be both compatible and conflicting")
        if self.decision == "same_offer" and self.selected_source is None:
            raise ValueError("same_offer requires an observed source")
        return self


class OfferReconciler(Protocol):
    def reconcile(self, *, observations: dict[str, object]) -> OfferReconciliationDecision: ...


def observation(item: ExtractedSupplierOfferItem, source: str) -> dict[str, object]:
    original = item.provider_observations.get(source)
    if isinstance(original, dict):
        return original
    return SupplierOfferObservation.model_validate(item.model_dump()).model_dump(mode="json")


def presentation(item: ExtractedSupplierOfferItem) -> tuple[Decimal | None, str | None]:
    size, unit = item.unit_size, normalize_text(item.unit) or None
    match = UNIT_PATTERN.search(item.raw_name)
    if match:
        size = size if size is not None else Decimal(match["size"].replace(",", "."))
        unit = unit or match["unit"].lower()
    unit = {
        "gr": "g",
        "grs": "g",
        "kgs": "kg",
        "lt": "l",
        "lts": "l",
        "cc": "ml",
        "un": "unit",
        "unidades": "unit",
    }.get(unit, unit)
    if size is not None and unit in {"kg", "l"}:
        size *= 1000
        unit = {"kg": "g", "l": "ml"}[unit]
    return size, unit


def package_quantity(item: ExtractedSupplierOfferItem) -> int | None:
    return item.package_quantity or parse_package_quantity(item.raw_name)


def name_tokens(item: ExtractedSupplierOfferItem) -> set[str]:
    name = UNIT_PATTERN.sub(" ", item.raw_name, count=1)
    return set(normalize_text(name).split()) - {"x", "de", "por"}


def agreement_score(left: ExtractedSupplierOfferItem, right: ExtractedSupplierOfferItem) -> Decimal:
    if normalize_text(left.raw_name) == normalize_text(right.raw_name):
        return Decimal("1")
    a, b = name_tokens(left), name_tokens(right)
    if not a or not b:
        return Decimal("0")
    if a == b:
        return Decimal("0.98")
    # Packaging descriptors may be omitted by one reader; never ignore product variants.
    packaging = {"sachet", "botella", "envase"}
    if a - packaging == b - packaging and len(a & b) >= 2:
        if presentation(left) == presentation(right) and presentation(left)[0] is not None:
            return Decimal("0.96")
    overlap = len(a & b) / max(len(a), len(b))
    spelling = SequenceMatcher(None, " ".join(sorted(a)), " ".join(sorted(b))).ratio()
    # Similar spelling alone is insufficient to automatically equate brands/variants.
    score = 0.65 * overlap + 0.35 * spelling
    return Decimal(str(round(min(score, 0.91), 4)))


def conflicting_fields(
    left: ExtractedSupplierOfferItem, right: ExtractedSupplierOfferItem
) -> list[str]:
    fields = []
    for field in ("offer_price", "currency", "price_type", "tax_included"):
        a, b = getattr(left, field), getattr(right, field)
        if a is not None and b is not None and a != b:
            fields.append(field)
    for field, a, b in zip(
        ("unit_size", "unit"), presentation(left), presentation(right), strict=True
    ):
        if a is not None and b is not None and a != b:
            fields.append(field)
    a, b = package_quantity(left), package_quantity(right)
    if a is not None and b is not None and a != b:
        fields.append("package_quantity")
    if left.brand and right.brand and normalize_text(left.brand) != normalize_text(right.brand):
        fields.append("brand")
    return fields


def completeness(item: ExtractedSupplierOfferItem) -> tuple:
    size, unit = presentation(item)
    return (
        sum(
            (
                bool(item.brand),
                size is not None and unit is not None,
                item.offer_price is not None,
                package_quantity(item) is not None,
            )
        ),
        len(normalize_text(item.raw_name)),
        item.confidence_score,
    )


def normalize_observation(item: ExtractedSupplierOfferItem) -> ExtractedSupplierOfferItem:
    result = item.model_copy(deep=True)
    size, unit = presentation(result)
    result.unit_size, result.unit = size, unit
    result.package_quantity = package_quantity(result)
    name = re.sub(r"\s+", " ", result.raw_name).strip()
    if size is not None and unit is not None:
        size_text = format(size, "f").rstrip("0").rstrip(".") if "." in str(size) else str(size)
        name = UNIT_PATTERN.sub(f"{size_text} {unit}", name, count=1)
        name = re.sub(r"\b[xX]\s*(?=\d)", "", name)
    name = name.title()
    name = re.sub(r"\b(Ml|Kg|G|L)\b", lambda m: m[0].lower(), name)
    if result.brand:
        brand = result.brand.strip().title()
        name = re.sub(
            r"(?<!\w)" + re.escape(result.brand) + r"(?!\w)", "", name, flags=re.IGNORECASE
        ).strip()
        name = f"{brand} {name}"
        result.brand = brand
    result.raw_name = re.sub(r"\s+", " ", name).strip()
    return result


def canonical_offer(
    left: ExtractedSupplierOfferItem,
    right: ExtractedSupplierOfferItem,
    left_source: str,
    right_source: str,
    score: Decimal,
) -> ExtractedSupplierOfferItem:
    # Stable tie break: independent of network completion order.
    source, base, other = max(
        [(left_source, left, right), (right_source, right, left)],
        key=lambda entry: (completeness(entry[1]), entry[0]),
    )
    selected = normalize_observation(base)
    other_normalized = normalize_observation(other)
    for field in ("brand", "unit_size", "unit", "package_quantity", "tax_included"):
        if getattr(selected, field) is None:
            setattr(selected, field, getattr(other_normalized, field))
    selected = normalize_observation(selected)
    selected.agreement_score = score
    selected.agreement_status = (
        "agreed_exact"
        if left.raw_name == right.raw_name and not conflicting_fields(left, right)
        else "agreed_normalized"
        if score >= Decimal("0.98")
        else "agreed_fuzzy"
    )
    selected.selected_source = source
    selected.resolved_by = "deterministic"
    selected.provider_observations = {
        left_source: observation(left, left_source),
        right_source: observation(right, right_source),
    }
    selected.notes = "; ".join(filter(None, [selected.notes, "confirmed_by_dual_read"]))
    return selected


def reconcile_items(
    observations: dict[str, list[ExtractedSupplierOfferItem]],
    jev: OfferReconciler | None = None,
) -> tuple[list[ExtractedSupplierOfferItem], list[dict[str, object]]]:
    """One-to-one pairs on the same known page. Unsafe pairs stay in the review ledger."""
    names = sorted(observations)
    if not names:
        return [], []
    left_source = names[0]
    right_source = names[1] if len(names) > 1 else ""
    left, right = observations[left_source], observations.get(right_source, [])
    edges = []
    for i, a in enumerate(left):
        for j, b in enumerate(right):
            if a.page_number is None or a.page_number != b.page_number:
                continue
            score = agreement_score(a, b)
            if score >= Decimal("0.70"):
                edges.append((score, i, j))
    used_left, used_right = set(), set()
    merged, unresolved = [], []
    for score, i, j in sorted(edges, key=lambda edge: (-edge[0], edge[1], edge[2])):
        if i in used_left or j in used_right:
            continue
        a, b = left[i], right[j]
        tied = any(
            s >= score and (li == i or rj == j) and (li, rj) != (i, j) for s, li, rj in edges
        )
        conflicts = conflicting_fields(a, b)
        if score >= Decimal("0.92") and not conflicts and not tied:
            merged.append(canonical_offer(a, b, left_source, right_source, score))
        else:
            status = "ambiguous"
            if any(field in conflicts for field in ("offer_price", "currency", "price_type")):
                status = "price_conflict"
            elif any(field in conflicts for field in ("unit_size", "unit", "package_quantity")):
                status = "presentation_conflict"
            audit = {
                "page_number": a.page_number,
                "agreement_score": float(min(score, Decimal("0.89")) if conflicts else score),
                "agreement_status": status,
                "selected_source": None,
                "resolved_by": "human_review",
                "conflicting_fields": conflicts,
                "provider_observations": {
                    left_source: observation(a, left_source),
                    right_source: observation(b, right_source),
                },
            }
            if status == "ambiguous" and jev is not None:
                try:
                    answer = OfferReconciliationDecision.model_validate(
                        jev.reconcile(observations=audit["provider_observations"])
                    )
                    if answer.selected_source and answer.selected_source not in observations:
                        raise ValueError("Jev selected an unavailable source")
                    audit["jev_shadow"] = answer.model_dump(mode="json")
                    audit["jev_shadow_status"] = (
                        "jev_shadow_agreement"
                        if answer.decision == "same_offer"
                        else "jev_shadow_disagreement"
                    )
                except Exception as exc:
                    audit["jev_shadow_error"] = type(exc).__name__
            unresolved.append(audit)
        used_left.add(i)
        used_right.add(j)
    for source, items, used in ((left_source, left, used_left), (right_source, right, used_right)):
        for index, item in enumerate(items):
            if index in used:
                continue
            selected = item.model_copy(deep=True)
            if selected.resolved_by != "human_review":
                selected.agreement_status = "single_source"
            selected.selected_source = source
            selected.provider_observations = {source: observation(item, source)}
            selected.notes = "; ".join(filter(None, [selected.notes, f"single_source={source}"]))
            merged.append(selected)
    return sorted(
        merged, key=lambda item: (item.page_number or 10**9, normalize_text(item.raw_name))
    ), unresolved
