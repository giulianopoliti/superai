from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum


class SupplierPriceType(StrEnum):
    UNIT = "unit"
    PACK = "pack"
    UNKNOWN = "unknown"


def normalize_offer_price(
    *,
    offer_price: Decimal,
    package_quantity: int | None,
    price_type: SupplierPriceType | str,
) -> Decimal | None:
    try:
        normalized_type = SupplierPriceType(price_type)
    except ValueError:
        return None

    if normalized_type == SupplierPriceType.UNIT:
        return offer_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if normalized_type == SupplierPriceType.PACK:
        if package_quantity is None or package_quantity <= 0:
            return None
        return (offer_price / Decimal(package_quantity)).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
    return None


def is_likely_pack_price(
    *,
    offer_price: Decimal,
    current_unit_cost: Decimal | None,
    package_quantity: int | None,
    tolerance: Decimal = Decimal("0.15"),
) -> bool:
    if (
        current_unit_cost is None
        or current_unit_cost <= 0
        or package_quantity is None
        or package_quantity <= 1
    ):
        return False

    expected_pack_price = current_unit_cost * Decimal(package_quantity)
    relative_difference = abs(offer_price - expected_pack_price) / expected_pack_price
    return relative_difference <= tolerance
