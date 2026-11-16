"""Commercial corroboration from observed reference prices, never catalog costs."""

import re
from decimal import Decimal

from app.modules.procurement.identity import physical_size
from app.modules.procurement.normalization import parse_argentine_decimal


_PROMOTION_COUNT = re.compile(r"(?<![\d.,])(\d{1,3})\s*[x×]\s*(\d{1,3})(?![\d.,])", re.I)
_PER_ITEM_PRICE = re.compile(
    r"\bprecio\s+x\s*u(?:n(?:idad)?)?\s*[:.]?\s*\$\s*(\d+(?:[.,]\d+)*)",
    re.I,
)


def promoted_pack_quantity(offer, block):
    """Corroborate a quantity promotion with the free units and printed unit price."""
    if len(block.proposals) != 1 or not offer.offer_price or offer.price_type == "unit":
        return None
    counts = _PROMOTION_COUNT.findall(offer.raw_name or "")
    if len(counts) != 1:
        return None
    delivered, charged = map(int, counts[0])
    if not 0 < charged < delivered or offer.package_quantity not in {None, delivered}:
        return None
    readable = "\n".join(f.text for f in block.fragments if f.legibility == "readable")
    if counts[0] not in _PROMOTION_COUNT.findall(readable):
        return None
    free_counts = re.findall(
        r"\bgratis\s+(\d{1,3})\s+\w+|\b(\d{1,3})\s+\w+\s+gratis\b", readable, re.I,
    )
    if {int(before or after) for before, after in free_counts} != {delivered - charged}:
        return None
    prices = {parse_argentine_decimal(price) for price in _PER_ITEM_PRICE.findall(readable)}
    if len(prices) != 1:
        return None
    unit_price = prices.pop()
    tolerance = Decimal("0.005") * delivered + Decimal("0.005")
    if unit_price and abs(unit_price * delivered - offer.offer_price) <= tolerance:
        return delivered
    return None


def reference_unit_basis(offer, block, evidence_text):
    """A per-litre/kg label can corroborate one retail container, not a bundle.

    At most one cent of reference rounding per reference unit is tolerated.
    Return None when the source cannot support the calculation.
    """
    if (
        offer.reference_price is None
        or offer.reference_price <= 0
        or offer.reference_unit not in {"kg", "l"}
        or offer.offer_price is None
    ):
        return None
    refs = offer.field_evidence.get("reference_price", [])
    literal = evidence_text(block, refs)
    unit_text = evidence_text(block, offer.field_evidence.get("reference_unit", []))
    if literal is None or unit_text is None:
        return None
    numbers = re.findall(r"\d+(?:[.,]\d+)*", literal)
    if len(numbers) != 1:
        return None
    # Brochure reference labels also use US decimal notation (3276.67).
    observed = (
        Decimal(numbers[0])
        if re.fullmatch(r"\d+\.\d{2}", numbers[0])
        else parse_argentine_decimal(numbers[0])
    )
    if observed != offer.reference_price:
        return None
    label = (
        r"(?:x\s*|por\s+)(?:kg|kilo(?:gramo)?s?)\b"
        if (offer.reference_unit == "kg")
        else r"(?:x\s*|por\s+)(?:lt?s?|litros?)\b"
    )
    if not re.search(label, unit_text, re.I):
        return None
    # Refuse a reference amount cited from another occurrence or a prior price.
    allowed = {f.fragment_id: f for f in block.fragments}
    for ref in refs:
        fragment = allowed.get(ref.fragment_id)
        if fragment is None or not re.search(label, fragment.text, re.I):
            return None
        labelled = re.findall(
            rf"{label}\s*:?\s*\$?\s*(\d+(?:[.,]\d+)*)", fragment.text, re.I
        )
        if numbers[0] not in labelled:
            return None
        if re.search(r"%|\b(?:antes|tachado|ahorro|descuento)\b", fragment.text, re.I):
            return None
    size = physical_size(offer.unit_size, offer.unit)
    expected_unit = "g" if offer.reference_unit == "kg" else "ml"
    if not size or size[1] != expected_unit or size[0] <= 0:
        return None
    if len(block.proposals) != 1 or offer.package_quantity:
        return None
    if any(
        re.search(r"\b(?:precio del pack|por bulto|por caja|pack de)\b", f.text, re.I)
        for f in block.fragments
    ):
        return None
    quantity = size[0] / Decimal(1000)
    tolerance = Decimal("0.005") * quantity + Decimal("0.005")
    return abs(offer.reference_price * quantity - offer.offer_price) <= tolerance
