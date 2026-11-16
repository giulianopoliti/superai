"""Commercial corroboration from observed reference prices, never catalog costs."""

import re
from decimal import Decimal

from app.modules.procurement.identity import physical_size
from app.modules.procurement.normalization import parse_argentine_decimal


def reference_unit_basis(offer, block, evidence_text):
    """A per-litre/kg label can corroborate one retail container, not a bundle.

    At most one cent of reference rounding per reference unit is tolerated.
    Multiple sizes/prices, promotions and unreadable citations remain unresolved.
    """
    if (
        offer.reference_price is None
        or offer.reference_price <= 0
        or offer.reference_unit not in {"kg", "l"}
        or offer.offer_price is None
    ):
        return False
    refs = offer.field_evidence.get("reference_price", [])
    literal = evidence_text(block, refs)
    unit_text = evidence_text(block, offer.field_evidence.get("reference_unit", []))
    if literal is None or unit_text is None:
        return False
    numbers = re.findall(r"\d+(?:[.,]\d+)*", literal)
    if len(numbers) != 1:
        return False
    # Brochure reference labels also use US decimal notation (3276.67).
    observed = (
        Decimal(numbers[0])
        if re.fullmatch(r"\d+\.\d{2}", numbers[0])
        else parse_argentine_decimal(numbers[0])
    )
    if observed != offer.reference_price:
        return False
    label = (
        r"(?:x\s*|por\s+)(?:kg|kilo(?:gramo)?s?)\b"
        if (offer.reference_unit == "kg")
        else r"(?:x\s*|por\s+)(?:lt?s?|litros?)\b"
    )
    if not re.search(label, unit_text, re.I):
        return False
    # Refuse a reference amount cited from another occurrence or a prior price.
    allowed = {f.fragment_id: f for f in block.fragments}
    for ref in refs:
        fragment = allowed.get(ref.fragment_id)
        if fragment is None or not re.search(label, fragment.text, re.I):
            return False
        labelled = re.findall(
            rf"{label}\s*:?\s*\$?\s*(\d+(?:[.,]\d+)*)", fragment.text, re.I
        )
        if numbers[0] not in labelled:
            return False
        if re.search(r"%|\b(?:antes|tachado|ahorro|descuento)\b", fragment.text, re.I):
            return False
    size = physical_size(offer.unit_size, offer.unit)
    expected_unit = "g" if offer.reference_unit == "kg" else "ml"
    if not size or size[1] != expected_unit or size[0] <= 0:
        return False
    if len(block.proposals) != 1 or offer.package_quantity:
        return False
    if any(
        re.search(r"\b(?:precio del pack|por bulto|por caja|pack de)\b", f.text, re.I)
        for f in block.fragments
    ):
        return False
    quantity = size[0] / Decimal(1000)
    tolerance = Decimal("0.005") * quantity + Decimal("0.005")
    return abs(offer.reference_price * quantity - offer.offer_price) <= tolerance
