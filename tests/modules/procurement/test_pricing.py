from decimal import Decimal

import pytest

from app.modules.procurement.normalization import parse_package_quantity
from app.modules.procurement.pricing import (
    SupplierPriceType,
    is_likely_pack_price,
    normalize_offer_price,
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("COCA COLA ZERO X6", 6),
        ("COCA COLA ZERO 6U", 6),
        ("JABON 3X125G", 3),
        ("JABON 125G X3", 3),
        ("PACK 12", 12),
        ("QUESO CREMA X290GRS", None),
    ],
)
def test_parse_package_quantity(name: str, expected: int | None) -> None:
    assert parse_package_quantity(name) == expected


def test_normalize_offer_price_handles_unit_pack_and_unknown() -> None:
    assert normalize_offer_price(
        offer_price=Decimal("2000"),
        package_quantity=6,
        price_type=SupplierPriceType.UNIT,
    ) == Decimal("2000.00")
    assert normalize_offer_price(
        offer_price=Decimal("12000"),
        package_quantity=6,
        price_type=SupplierPriceType.PACK,
    ) == Decimal("2000.00")
    assert (
        normalize_offer_price(
            offer_price=Decimal("12000"),
            package_quantity=6,
            price_type=SupplierPriceType.UNKNOWN,
        )
        is None
    )


def test_likely_pack_price_requires_quantity_and_matching_ratio() -> None:
    assert is_likely_pack_price(
        offer_price=Decimal("12000"),
        current_unit_cost=Decimal("2050"),
        package_quantity=6,
    )
    assert not is_likely_pack_price(
        offer_price=Decimal("12000"),
        current_unit_cost=Decimal("2050"),
        package_quantity=None,
    )
