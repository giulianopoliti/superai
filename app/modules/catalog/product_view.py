"""Auxiliary catalog contract. Original POS fields remain authoritative source data."""

from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, Field

from app.modules.procurement.normalization import normalize_text

CATALOG_VERSION = "catalog-full-v3"
PROMPT_HEADER = "# catalog_full_interpretation v3"
COMPATIBLE_PROMPT_HEADERS = (PROMPT_HEADER, "# catalog_decisions_first v1")
FILTER_ATTRIBUTES = {
    "category", "subcategory", "brand", "product_type", "line", "variant", "packaging",
    "content", "package_quantity", "minimum_order_quantity",
}
CatalogAttribute = Literal[
    "category", "subcategory", "brand", "product_type", "line", "variant", "packaging",
    "content", "package_quantity", "minimum_order_quantity",
]


class AuxiliaryProduct(BaseModel):
    interpretation_id: str
    source_hash: str
    config_hash: str
    version: str = CATALOG_VERSION
    attributes: dict = Field(default_factory=dict)
    aliases: dict[str, list[str]] = Field(default_factory=dict)
    standard_name: str = ""

    def value(self, attribute):
        state = self.attributes.get(attribute, {})
        # Row status may be partial/needs_review. Trust only each validated field.
        if state.get("status") != "interpreted" or not state.get("evidence"):
            return None
        return state.get("value")

    def terms(self, attribute):
        value = self.value(attribute)
        if not isinstance(value, str) or not value.strip():
            return set()
        return {normalize_text(v) for v in [value, *self.aliases.get(attribute, [])] if v}

    def content(self):
        value = self.value("content")
        if not isinstance(value, dict):
            return None
        try:
            amount = Decimal(str(value["value"]))
            unit = value["unit"]
            if amount.is_finite() and amount > 0 and unit in {"g", "kg", "ml", "l", "unit"}:
                return amount, unit
        except (KeyError, InvalidOperation, TypeError, ValueError):
            pass
        return None

    def matches_filter(self, attribute, requested):
        if attribute == "content":
            from app.modules.procurement.identity import physical_size
            from app.modules.procurement.normalization import parse_unit

            content = self.content()
            requested_size = physical_size(*parse_unit(requested))
            return bool(content and requested_size and physical_size(*content) == requested_size)
        if attribute in {"package_quantity", "minimum_order_quantity"}:
            return requested.strip().isdigit() and self.value(attribute) == int(requested.strip())
        return normalize_text(requested) in self.terms(attribute)

    def search_names(self):
        # Rebuild from accepted fields: stored standard_name can include unresolved proposals.
        pieces = [self.value(k) for k in ("product_type", "brand", "line", "variant")]
        content = self.content()
        if content:
            pieces.append(f"{content[0].normalize():f} {content[1]}")
        count = self.value("package_quantity")
        if count:
            pieces.append(f"pack x{count}")
        name = " ".join(p for p in pieces if isinstance(p, str) and p)
        names = [name] if name else []
        for attribute in ("product_type", "brand", "line", "variant"):
            value = self.value(attribute)
            if isinstance(value, str) and value:
                names.extend(
                    name.replace(value, alias) for alias in self.aliases.get(attribute, [])
                )
        return names


def product_search_names(product):
    return list(dict.fromkeys([
        product.name, getattr(product, "normalized_name", None) or product.name,
        *(product.auxiliary.search_names() if getattr(product, "auxiliary", None) else []),
    ]))
