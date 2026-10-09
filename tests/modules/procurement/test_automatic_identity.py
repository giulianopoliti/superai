from decimal import Decimal

import pytest

from app.modules.procurement.matching import ProductMatchService
from app.modules.procurement.normalization import (
    normalize_text,
    parse_argentine_decimal,
    parse_unit,
)
from app.modules.procurement.schemas import Product, SupplierOfferItem


def pair(offer_name, product_name, **item_kwargs):
    item = SupplierOfferItem(
        business_id="b",
        supplier_id="s",
        supplier_offer_document_id="d",
        raw_name=offer_name,
        normalized_name=normalize_text(offer_name),
        offer_price=200,
        **item_kwargs,
    )
    product = Product(
        business_id="b",
        name=product_name,
        normalized_name=normalize_text(product_name),
        current_cost=100,
    )
    return item, product


@pytest.mark.parametrize("name", ["Alimento 1.5KG", "Alimento 1,5KG"])
def test_presentations_keep_decimal_separator(name):
    assert parse_unit(name) == (Decimal("1.5"), "kg")
    assert parse_argentine_decimal("1.500,25") == Decimal("1500.25")


def test_exact_identity_is_accepted_even_when_not_worth_buying():
    item, product = pair("ALA detergente limon 300ml", "ALA DETERGENTE LIMON 300ML", brand="ALA")
    [candidate] = ProductMatchService(None).match_item(item=item, products=[product])
    assert candidate.auto_accepted
    assert candidate.recommendation == "do_not_buy"


def test_kimbies_contents_match_without_accepting_unresolved_price():
    item, product = pair(
        "KIMBIES Toallas humedas x48u",
        "KIMBIES TOALLITAS HUMEDAS X48U",
        brand="KIMBIES",
        price_type="unknown",
        metadata={"pack_review": {"status": "needs_review"}},
        unit_size=48,
        unit="u",
    )
    product.unit_size, product.unit = Decimal(48), "unit"
    [candidate] = ProductMatchService(None).match_item(item=item, products=[product])
    assert candidate.auto_accepted
    assert candidate.recommendation == "review"
    assert candidate.cost_difference is None


@pytest.mark.parametrize(
    "offered,catalog",
    [
        ("CIF limpiador gatillo 500ml", "CIF limpiador gatillo BAÑO 500ml"),
        ("DOVE barra 50g", "DOVE aerosol original 150ml"),
        ("DOG CHOW ADULTO 15KG", "DOG CHOW ADULTO 1.5KG"),
        ("Detergente", "Detergente"),
    ],
)
def test_missing_variant_or_different_presentation_needs_review(offered, catalog):
    item, product = pair(offered, catalog)
    [candidate] = ProductMatchService(None).match_item(item=item, products=[product])
    assert not candidate.auto_accepted


def test_two_equivalent_catalog_rows_are_not_auto_accepted():
    item, product = pair("Arroz Gallo 1kg", "Arroz Gallo 1kg", brand="Gallo")
    other = product.model_copy(update={"id": "other"})
    [candidate] = ProductMatchService(None).match_item(item=item, products=[product, other])
    assert not candidate.auto_accepted


def test_old_corrupted_catalog_size_cannot_confirm_or_calculate_savings():
    item, product = pair("DOG CHOW 15KG", "DOG CHOW 1.5KG", unit_size=15, unit="kg")
    product.unit_size, product.unit = Decimal(15), "kg"
    candidate = ProductMatchService(None)._score_product(item, product)
    assert "same_unit_size" not in candidate.reasons
    candidate = ProductMatchService._with_commercials(candidate)
    assert not candidate.auto_accepted
    assert candidate.recommendation == "review"
    assert candidate.cost_difference is None


def test_other_business_and_reader_conflicts_cannot_auto_accept():
    item, product = pair("Arroz Gallo 1kg", "Arroz Gallo 1kg", brand="Gallo")
    product.business_id = "other-business"
    [candidate] = ProductMatchService(None).match_item(item=item, products=[product])
    assert candidate.product is None
    product.business_id = "b"
    item.metadata["resolved_by"] = "human_review"
    [candidate] = ProductMatchService(None).match_item(item=item, products=[product])
    assert not candidate.auto_accepted


@pytest.mark.parametrize(
    "offered,catalog",
    [
        ("AYUDIN Lavandina original x2lt.", "LAVANDINA AYUDIN 2L ORIGINAL"),
        ("NATURA Mayonesa sachet x125gr.", "NATURA MAYONESA 125G"),
        ("COLGATE Crema dental ultra blanco x90g", "COLGATE ULTRA BLANCO 90G"),
    ],
)
def test_vital_canonical_identity_ignores_order_and_narrow_category_aliases(offered, catalog):
    item, product = pair(offered, catalog, price_type="unknown")
    [candidate] = ProductMatchService(None).match_item(item=item, products=[product])
    assert candidate.auto_accepted
    assert candidate.recommendation == "review"
    assert candidate.cost_difference is None


def test_exact_original_wins_over_fuzzy_lavanda_and_flour_grade_is_not_a_runner():
    item, original = pair("AYUDIN Lavandina original x2lt.", "LAVANDINA AYUDIN 2L ORIGINAL")
    lavanda = original.model_copy(
        update={
            "id": "lavanda",
            "name": "AYUDIN LAVANDINA LAVANDA 2L",
            "normalized_name": "ayudin lavandina lavanda 2l",
        }
    )
    [candidate] = ProductMatchService(None).match_item(item=item, products=[lavanda, original])
    assert candidate.product.id == original.id
    assert candidate.auto_accepted
    item, flour = pair("FAVORITA Harina 000 x1kg.", "FAVORITA HARINA 000 1KG")
    fine = flour.model_copy(
        update={
            "id": "0000",
            "name": "FAVORITA HARINA 0000 1KG",
            "normalized_name": "favorita harina 0000 1kg",
        }
    )
    [candidate] = ProductMatchService(None).match_item(item=item, products=[fine, flour])
    assert candidate.product.id == flour.id
    assert candidate.auto_accepted


@pytest.mark.parametrize(
    "offered,catalog",
    [
        ("NATURA Mayonesa sachet 125g", "NATURA MAYONESA FRASCO 125G"),
        ("COLGATE Crema dental Ultra blanco 90g", "COLGATE TOTAL CARBON 90G"),
        ("DOVE reconst minoacido 200ml", "DOVE RECONSTRUCCION 200ML"),
        ("CIF Detergente 300ml", "CIF DETERGENTE LIMON 300ML"),
        ("SKIP liquido 3L", "SKIP LIQUIDO 800ML"),
        ("ALMA MORA Malbec 750ml", "ALMA MORA BLANCO DULCE 750ML"),
    ],
)
def test_aliases_do_not_invent_variant_size_or_package_equivalence(offered, catalog):
    item, product = pair(offered, catalog)
    [candidate] = ProductMatchService(None).match_item(item=item, products=[product])
    assert not candidate.auto_accepted


def test_duplicate_alias_equivalents_do_not_become_unique_by_word_order():
    item, product = pair("NATURA Mayonesa sachet 125g", "NATURA MAYONESA 125G")
    duplicate = product.model_copy(
        update={
            "id": "duplicate",
            "name": "MAYONESA NATURA SACHET 125G",
            "normalized_name": "mayonesa natura sachet 125g",
        }
    )
    [candidate] = ProductMatchService(None).match_item(item=item, products=[product, duplicate])
    assert not candidate.auto_accepted
