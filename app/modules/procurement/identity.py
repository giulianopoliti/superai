"""Conservative identity checks, independent of price and purchasing."""

import re
from functools import lru_cache

from app.modules.procurement.normalization import UNIT_PATTERN, normalize_text, parse_unit

AUTO_ACCEPT_REASON = "auto_accepted_identity_v1"
IDENTITY_RULE_VERSION = "canonical_identity_v2"


def physical_size(size, unit):
    unit = {"u": "unit", "un": "unit", "unidades": "unit"}.get(unit, unit)
    canonical, factor = {"kg": ("g", 1000), "l": ("ml", 1000), "cc": ("ml", 1)}.get(unit, (unit, 1))
    return (size * factor, canonical) if size is not None and unit else None


def inconsistent_size(name, size, unit):
    parsed_size, parsed_unit = parse_unit(name)
    parsed = physical_size(parsed_size, parsed_unit)
    stored = physical_size(size, unit)
    return bool(parsed and stored and parsed != stored)


@lru_cache(maxsize=8192)
def _identity_token_tuple(name):
    # Canonicalize amounts before punctuation removal, preserving decimals.
    def amount(match):
        size, unit = parse_unit(match.group())
        size, unit = physical_size(size, unit)
        return f" {format(size.normalize(), 'f')} {unit} "

    name = re.sub(r"[xX](?=\d)", " ", name)
    name = UNIT_PATTERN.sub(amount, name)
    name = re.sub(r"(\d)\s*(?:unidades|unid|uni|un|u)\b", r"\1 u", name, flags=re.I)
    tokens = normalize_text(name).split()
    # Narrow, versioned category aliases. Never remove flavors, formulas or counts.
    if "mayonesa" in tokens:
        tokens = [token for token in tokens if token != "sachet"]
    if "colgate" in tokens:
        for index in range(len(tokens) - 1):
            if tokens[index : index + 2] == ["crema", "dental"]:
                tokens = tokens[:index] + tokens[index + 2 :]
                break
    aliases = {"toallitas": "toallas", "grs": "g", "gr": "g", "lts": "l", "lt": "l"}
    return tuple(sorted(aliases.get(token, token) for token in tokens if token != "x"))


def identity_tokens(name):
    # Only immutable text is cached, never products, tenant decisions or feedback.
    return list(_identity_token_tuple(name))


def identity_conflicts(item, product) -> list[str]:
    """Hard contradictions, also applied when reusing human feedback.

    Missing attributes are not contradictions. Explicit variants are read from
    metadata when available; known opposing variants in names remain vetoes.
    """
    reasons = []
    if product.business_id != item.business_id:
        reasons.append("tenant_mismatch")
    if not getattr(product, "active", True):
        reasons.append("inactive_product")
    if item.brand and product.brand and normalize_text(item.brand) != normalize_text(product.brand):
        reasons.append("brand_conflict")
    for name, size, unit in (
        (item.raw_name, item.unit_size, item.unit),
        (product.name, product.unit_size, product.unit),
    ):
        if inconsistent_size(name, size, unit):
            reasons.append("presentation_inconsistent")
    item_size = physical_size(item.unit_size, item.unit) or physical_size(
        *parse_unit(item.raw_name)
    )
    product_size = physical_size(product.unit_size, product.unit) or physical_size(
        *parse_unit(product.name)
    )
    if item_size and product_size and item_size != product_size:
        reasons.append("presentation_conflict")
    item_tokens = set(identity_tokens(item.raw_name))
    product_tokens = set(identity_tokens(product.name))
    opposing_variants = (
        {"suave", "tradicional"},
        {"zero", "original", "clasica", "clasico"},
        {"adulto", "cachorro"},
        {"limon", "lavanda"},
        {"bano", "cocina"},
        {"aerosol", "barra", "rollon"},
        {"000", "0000"},
        {"original", "lavanda", "limon", "glaciar"},
        {"shampoo", "acondicionador"},
        {"liquido", "polvo"},
        {"fideos", "salsa"},
        {"malbec", "cabernet", "syrah", "merlot", "blanco", "rosado", "chardonnay"},
    )
    if any(
        item_tokens & group and product_tokens & group and not item_tokens & product_tokens & group
        for group in opposing_variants
    ):
        reasons.append("variant_conflict")
    # Package descriptors are retained for contradictions even when an approved
    # category alias omits one from the canonical name.
    forms = {"sachet", "frasco", "doypack", "pote"}
    item_forms = set(normalize_text(item.raw_name).split()) & forms
    product_forms = set(normalize_text(product.name).split()) & forms
    if item_forms and product_forms and not item_forms & product_forms:
        reasons.append("presentation_conflict")
    variant = getattr(item, "metadata", {}).get("variant")
    if isinstance(variant, str) and variant.strip():
        if not set(identity_tokens(variant)) <= product_tokens:
            reasons.append("variant_conflict")
    return list(dict.fromkeys(reasons))


def offer_identity_tokens(item, *, commercial_pack_verified=False):
    """Remove only a validated commercial xN suffix, never retail contents or minimums."""
    name = item.raw_name
    quantity = getattr(item, "package_quantity", None)
    typed_pack = getattr(item, "price_type", None) == "pack"
    verified = (
        not hasattr(item, "price_status")
        or item.price_status == "confirmed"
        or commercial_pack_verified
    )
    size = physical_size(item.unit_size, item.unit)
    parsed = physical_size(*parse_unit(name))
    if typed_pack and verified and quantity and quantity >= 2 and size and parsed == size:
        name = re.sub(rf"\s+[xX]\s*{quantity}\s*$", "", name)
    return identity_tokens(name)


def can_accept_identity(candidate, runner_up=None):
    item, product = candidate.supplier_offer_item, candidate.product
    if product is None or identity_conflicts(item, product):
        return False
    if item.metadata.get("resolved_by") == "human_review":
        return False
    if inconsistent_size(product.name, product.unit_size, product.unit) or inconsistent_size(
        item.raw_name, item.unit_size, item.unit
    ):
        return False
    if item.brand and product.brand and normalize_text(item.brand) != normalize_text(product.brand):
        return False
    item_size = physical_size(item.unit_size, item.unit)
    product_size = physical_size(product.unit_size, product.unit)
    if item_size and product_size and item_size != product_size:
        return False
    if not canonical_identity_equal(item, product):
        return False
    if runner_up is not None:
        if runner_up.product and canonical_identity_equal(item, runner_up.product):
            return False
    return True


def canonical_identity_equal(item, product):
    """Exact, compatible identity independent of fuzzy score and word order.

    Explicit physical size or counted retail content is required. Unspecified
    variants still differ in canonical tokens; missing data is not equality.
    """
    if product is None or not item.raw_name or identity_conflicts(item, product):
        return False
    tokens = offer_identity_tokens(item)
    if not tokens or tokens != identity_tokens(product.name):
        return False
    size = physical_size(*parse_unit(item.raw_name))
    contents = re.search(r"(?:\b|(?<=[xX]))\d+\s*(?:unidades|unid|uni|un|u)\b", item.raw_name, re.I)
    # Keep the legacy exact xN catalogue convention for identities only. V2
    # requires typed presentation evidence; this never supplies a pack divisor.
    legacy_count = (
        not hasattr(item, "price_status")
        and "analysis_v2" not in getattr(item, "metadata", {})
        and re.search(r"\b[xX]\s*\d+\b", item.raw_name)
    )
    return bool(size or contents or legacy_count)
