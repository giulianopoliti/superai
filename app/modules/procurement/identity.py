"""Conservative identity checks, independent of price and purchasing."""

import re
from functools import lru_cache

from app.modules.procurement.normalization import UNIT_PATTERN, normalize_text, parse_unit

AUTO_ACCEPT_REASON = "auto_accepted_identity_v1"
IDENTITY_RULE_VERSION = "canonical_identity_v3"


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


def _offer_interpretation(item):
    analysis = getattr(item, "metadata", {}).get("analysis_v2", {})
    interpretation = analysis.get("interpretation", {}) if isinstance(analysis, dict) else {}
    return interpretation if isinstance(interpretation, dict) else {}


def _offer_identity_attributes(item):
    values = getattr(item, "identity_attributes", {}) or _offer_interpretation(item).get(
        "identity_attributes", {}
    )
    return values if isinstance(values, dict) else {}


def identity_conflicts(item, product) -> list[str]:
    """Hard contradictions, also applied when reusing human feedback.

    Missing attributes are not contradictions. Explicit variants are read from
    metadata when available; known opposing variants in names remain vetoes.
    """
    reasons = []
    auxiliary = getattr(product, "auxiliary", None)
    if auxiliary:
        interpretation = _offer_interpretation(item)
        identity_attributes = _offer_identity_attributes(item)
        for attribute in ("brand", "product_type", "line", "variant", "packaging"):
            observed = identity_attributes.get(attribute)
            if attribute == "brand":
                observed = item.brand or observed
            observed = observed or getattr(item, "metadata", {}).get(attribute)
            terms = auxiliary.terms(attribute)
            if isinstance(observed, str) and terms and normalize_text(observed) not in terms:
                reasons.append(attribute + "_conflict")
        size = physical_size(item.unit_size, item.unit) or physical_size(
            *parse_unit(item.raw_name or "")
        )
        content = auxiliary.content()
        if size and content and size != physical_size(*content):
            reasons.append("presentation_conflict")
        retail_count = getattr(item, "retail_contents_count", None) or interpretation.get(
            "retail_contents_count"
        )
        count = auxiliary.value("package_quantity")
        if retail_count is not None and count is not None and retail_count != count:
            reasons.append("retail_count_conflict")
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
        {"fideos", "arroz", "premezcla", "pan"},
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
        if (not set(identity_tokens(variant)) <= product_tokens
                and not (auxiliary and normalize_text(variant) in auxiliary.terms("variant"))):
            reasons.append("variant_conflict")
    return list(dict.fromkeys(reasons))


def offer_identity_tokens(item, *, commercial_pack_verified=False):
    """Remove only a validated commercial xN suffix, never retail contents or minimums."""
    name = item.raw_name or ""
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
    tokens = identity_tokens(name)
    fields = getattr(item, "field_evidence", {}) or _offer_interpretation(item).get(
        "field_evidence", {}
    )
    if item.brand and fields.get("brand"):
        tokens = sorted(set(tokens) | set(identity_tokens(item.brand)))
    if (fields.get("unit_size") and not physical_size(*parse_unit(name))
            and physical_size(item.unit_size, item.unit)):
        tokens = sorted(set(tokens) | set(identity_tokens(f"{item.unit_size} {item.unit}")))
    return tokens


def is_multi_product_offer(item):
    text = normalize_text(item.raw_name or "")
    return bool(
        {"shampoo", "acondicionador"} <= set(text.split())
        or re.search(r"\b(?:varias variedades|todas las variedades|varios sabores)\b", text)
    )


def retrieval_identity_tokens(item):
    """A search projection only; it never establishes equivalence."""
    tokens = set(offer_identity_tokens(item))
    tokens.update(identity_tokens(item.brand or ""))
    if item.unit_size is not None and item.unit:
        tokens.update(identity_tokens(f"{item.unit_size} {item.unit}"))
    # Category synonyms improve recall without deleting any identity discriminator.
    for group in ({"detergente", "lavavajillas"}, {"acond", "acondicionador"}):
        if tokens & group:
            tokens.update(group)
    return tokens


def identity_discriminator_gaps(item, product, alternatives=()):
    """Protect original catalog variants even when auxiliary attributes are absent.

    Named lines/flavors are required even with one retrieved candidate. Unknown
    differences between sibling catalog names also require observed evidence.
    """
    observed = retrieval_identity_tokens(item)
    named = set(identity_tokens(product.name))
    protected = {
        "1890", "ipa", "stout", "zero", "light", "sin", "alcohol", "bioactive",
        "limon", "lima", "lavanda", "original", "clasica", "clasico", "suave",
        "tradicional", "ceramidas", "balance", "colageno", "restauracion",
        "integral", "pizza", "000", "0000", "antigrasa", "bano", "cocina",
        "sachet", "doypack", "gatillo", "frasco", "pote",
    }
    gaps = []
    brand = set(identity_tokens(item.brand or ""))
    if brand and not brand <= named | set(identity_tokens(product.brand or "")):
        gaps.append("catalog_brand_not_supported")
    if product.brand and not set(identity_tokens(product.brand)) <= observed:
        gaps.append("catalog_brand_not_observed")
    if (named & protected) - observed:
        gaps.append("catalog_discriminator_not_observed")
    semantic_type_terms = {"de", "del", "x"}
    for group in (
        {"detergente", "lavavajillas"},
        {"desodorante", "antitranspirante"},
    ):
        if observed & group:
            semantic_type_terms.update(group)
    if observed & {"dental", "dentifrico", "dientes"}:
        semantic_type_terms.update({"pasta", "dientes", "crema", "dental", "dentifrico"})
    if named - observed - semantic_type_terms:
        # Unknown original-name details may be a line or variety. No confidence
        # score can supply them, including when retrieval found only one SKU.
        gaps.append("catalog_detail_not_observed")
    # Siblings are grouped before retrieval truncation. Generic base labels must
    # not win by omission when another named line is available.
    structural = {"cerveza", "lata", "detergente", "lavavajillas", "de", "del", "x"}
    for other in alternatives:
        if other.id == getattr(product, "id", getattr(product, "product_id", None)):
            continue
        other_tokens = set(identity_tokens(other.name))
        differences = (named ^ other_tokens) - structural
        if differences and not differences & observed:
            gaps.append("catalog_family_underspecified")
            break
    return list(dict.fromkeys(gaps))


def _scoped_descriptor_equal(item_tokens, product_tokens):
    """BLANCAFLOR leavening flour: optional fortification wording, no global stopword."""
    left, right = set(item_tokens), set(product_tokens)
    context = {"blancaflor", "harina", "leudante"}
    return context <= left & right and left ^ right == {"fortificada"}


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


def auxiliary_identity_gaps(item, product):
    """Missing discriminators require review, without calling them contradictions."""
    auxiliary = getattr(product, "auxiliary", None)
    if not auxiliary:
        return []
    tokens = set(identity_tokens(item.raw_name or ""))
    attributes = _offer_identity_attributes(item)
    gaps = []
    for attribute in ("line", "variant"):
        terms = auxiliary.terms(attribute)
        observed = attributes.get(attribute) or getattr(item, "metadata", {}).get(attribute)
        if terms and not (
            observed and normalize_text(observed) in terms
            or any(set(identity_tokens(term)) <= tokens for term in terms)
        ):
            gaps.append("auxiliary_" + attribute + "_not_observed")
    count = auxiliary.value("package_quantity")
    retail_count = getattr(item, "retail_contents_count", None) or _offer_interpretation(item).get(
        "retail_contents_count"
    )
    if count and count > 1 and not (
        retail_count == count
        or re.search(rf"\bpack\s*(?:x|de)?\s*{count}\b", item.raw_name or "", re.I)
    ):
        gaps.append("auxiliary_retail_pack_not_observed")
    return gaps


def catalog_variant_ambiguity(item, product, alternatives):
    """An exact base name does not distinguish an otherwise identical named variant."""
    if not canonical_identity_equal(item, product):
        return False
    tokens = set(offer_identity_tokens(item))
    size = physical_size(item.unit_size, item.unit) or physical_size(*parse_unit(item.raw_name))
    if not size:
        return False
    for other in alternatives:
        if other.id == product.id or other.business_id != item.business_id or not other.active:
            continue
        other_size = physical_size(other.unit_size, other.unit) or physical_size(
            *parse_unit(other.name)
        )
        if not other_size and other.auxiliary and other.auxiliary.content():
            other_size = physical_size(*other.auxiliary.content())
        if other_size != size:
            continue
        names = [other.name]
        if other.auxiliary:
            names.extend(other.auxiliary.search_names())
        if any(tokens < set(identity_tokens(name)) for name in names):
            if not identity_conflicts(item, other):
                return True
    return False


def canonical_identity_equal(item, product, *, known_conflicts=None):
    """Exact, compatible identity independent of fuzzy score and word order.

    Explicit physical size or counted retail content is required. Unspecified
    variants still differ in canonical tokens; missing data is not equality.
    """
    if product is None or not item.raw_name:
        return False
    if known_conflicts is None:
        known_conflicts = identity_conflicts(item, product)
    if known_conflicts:
        return False
    tokens = offer_identity_tokens(item)
    if auxiliary_identity_gaps(item, product):
        return False
    auxiliary = getattr(product, "auxiliary", None)
    identity_names = [product.name]
    # Taxonomy/type and size alone cannot establish an auxiliary identity.
    if auxiliary and auxiliary.value("brand") and auxiliary.content():
        identity_names.extend(auxiliary.search_names())
    # Reader schemas separate brand from the visible product name. Compare
    # those fields jointly, without removing variants or package descriptors.
    fields = getattr(item, "field_evidence", {}) or _offer_interpretation(item).get(
        "field_evidence", {}
    )
    if item.brand and fields.get("brand"):
        def clean(values):
            return sorted(t for t in values if t not in {"de", "del"})
        combined = list(tokens)
        for token in identity_tokens(item.brand):
            if token not in combined:
                combined.append(token)
        if any(
            clean(combined) == clean(identity_tokens(name))
            or _scoped_descriptor_equal(clean(combined), clean(identity_tokens(name)))
            for name in identity_names
        ):
            return bool(physical_size(item.unit_size, item.unit))
    if not tokens or not any(
        tokens == identity_tokens(name) for name in identity_names
    ):
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
