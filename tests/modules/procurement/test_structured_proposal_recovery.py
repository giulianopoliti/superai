from decimal import Decimal

import pytest

from app.modules.procurement.analysis_contracts import (
    EvidenceFragment,
    OfferDecisionRequest,
    OfferTextBlock,
    ReaderOfferProposalV2,
)
from app.modules.procurement.decision_policy import DocumentDecisionPolicy, observed_offer_price
from app.modules.procurement.document_analysis import StructuredProposalAssembler, usable_evidence


def block(text, *, quote="$749", extra=None, conditions=None, condition_refs=None):
    name = "FAVORITA Harina 000 x1kg."
    evidence = [
        {"field": "raw_name", "refs": [{"scope": "block", "fragment_index": 0, "quote": name}]},
        {"field": "offer_price", "refs": [{"scope": "block", "fragment_index": 0, "quote": quote}]},
    ]
    proposal = ReaderOfferProposalV2(
        raw_name=name,
        brand=None,
        unit_size=None,
        unit=None,
        offer_price=Decimal("749"),
        currency="ARS",
        tax_included=True,
        price_type="unknown",
        package_quantity=None,
        retail_contents_count=None,
        minimum_purchase=None,
        confidence_score=None,
        field_evidence=evidence + (extra or []),
        warnings=[],
    )
    return OfferTextBlock(
        block_id="b",
        run_id="r",
        reading_id="read",
        page_number=1,
        order_on_page=0,
        full_text=text,
        fragments=[EvidenceFragment(fragment_id="f", text=text, order=0, legibility="readable")],
        proposals=[proposal],
        page_conditions=conditions or [],
        condition_refs=condition_refs or [],
    )


def assemble(source):
    return (
        StructuredProposalAssembler()
        .assemble_interpretation(
            business_id="tenant",
            block=source,
            classification=None,
            candidates=[],
        )
        .offers[0]
    )


def test_optional_tax_bad_literal_preserves_name_and_amount():
    source = block(
        "$749 FAVORITA Harina 000 x1kg.",
        extra=[
            {
                "field": "tax_included",
                "refs": [{"scope": "page_condition", "fragment_index": 0, "quote": "Iva incluido"}],
            },
        ],
        conditions=[
            EvidenceFragment(fragment_id="c", text="IVA INCLUIDO", order=0, legibility="readable")
        ],
        condition_refs=["c"],
    )
    offer = assemble(source)
    assert offer.raw_name == source.proposals[0].raw_name
    assert observed_offer_price(offer=offer, block=source) == Decimal("749")
    assert offer.tax_included is None
    assert offer.reason_codes == ["tax_included_invalid_reference"]
    assert source.proposals[0].tax_included is True  # Original input remains immutable.


@pytest.mark.parametrize("invalid", ["foreign_scope", "foreign_index", "duplicate"])
def test_bad_required_name_reference_does_not_publish_that_name(invalid):
    source = block("$749 FAVORITA Harina 000 x1kg.")
    if invalid == "foreign_scope":
        source.page_conditions = [
            EvidenceFragment(
                fragment_id="c", text=source.proposals[0].raw_name, order=0, legibility="readable"
            )
        ]
        source.proposals[0].field_evidence[0].refs[0].scope = "page_condition"
    elif invalid == "foreign_index":
        source.proposals[0].field_evidence[0].refs[0].fragment_index = 9
    else:
        source.proposals[0].field_evidence.append(
            source.proposals[0].field_evidence[0].model_copy()
        )
    offer = assemble(source)
    assert offer.raw_name is None
    assert offer.offer_price == Decimal("749")
    assert "raw_name" not in offer.field_evidence


@pytest.mark.parametrize(
    "text,offset",
    [
        ("DESDE 3 UNIDADES $749 FAVORITA Harina 000 x1kg. PRECIO XKG: $749", 17),
        ("PRECIO XKG: $749 FAVORITA Harina 000 x1kg. PRECIO ACTUAL: $749", 58),
        ("Antes $749, ahora $749 FAVORITA Harina 000 x1kg.", 18),
    ],
)
def test_repeated_money_is_selected_by_role_not_first_occurrence(text, offset):
    source = block(text)
    offer = assemble(source)
    ref = offer.field_evidence["offer_price"][0]
    assert ref.start == offset
    assert source.fragments[0].text[ref.start : ref.end] == "$749"
    assert observed_offer_price(offer=offer, block=source) == Decimal("749")


def test_two_unlabelled_same_prices_remain_ambiguous():
    source = block("$749 FAVORITA Harina 000 x1kg. Otra variante $749")
    offer = assemble(source)
    assert offer.field_evidence["offer_price"][0].start is None
    assert observed_offer_price(offer=offer, block=source) is None


def test_money_substring_does_not_identify_a_longer_number():
    source = block("$749,50 FAVORITA Harina 000 x1kg.")
    offer = assemble(source)
    assert offer.offer_price is None
    assert "offer_price_invalid_reference" in offer.reason_codes


def test_partial_reference_cannot_back_an_optional_field():
    source = block(
        "$749 FAVORITA Harina 000 x1kg.",
        extra=[
            {
                "field": "tax_included",
                "refs": [{"scope": "page_condition", "fragment_index": 0, "quote": "IVA incluido"}],
            },
        ],
        conditions=[
            EvidenceFragment(fragment_id="c", text="IVA incluido", order=0, legibility="partial")
        ],
        condition_refs=["c"],
    )
    assert assemble(source).tax_included is None


@pytest.mark.parametrize("separator", ["\n", "\r\n", "\t", "  ", "\u00a0"])
def test_name_whitespace_recovery_keeps_original_quote_and_offsets(separator):
    original = separator.join(["FAVORITA", "Harina", "000", "x1kg."])
    source = block("$749 " + original)
    offer = assemble(source)
    ref = offer.field_evidence["raw_name"][0]
    assert offer.raw_name == "FAVORITA Harina 000 x1kg."
    assert ref.quote == original
    assert source.fragments[0].text[ref.start:ref.end] == original
    assert ref.fragment_id == "f"
    assert offer.reason_codes == []
    assert source.proposals[0].field_evidence[0].refs[0].quote == offer.raw_name
    assert observed_offer_price(offer=offer, block=source) == Decimal("749")


@pytest.mark.parametrize("original", [
    "FAVORITA\nHarina\n0000 x1kg.",
    "FAVORITA\nHarina\n000 x2kg.",
    "Favorita\nHarina\n000 x1kg.",
    "FAVORITA\nHarina\n000 x1kg",
    "FAVORITA\nHarina EXTRA\n000 x1kg.",
    "FAVORITA\nHarina\n000 x1kg. y FAVORITA\tHarina\t000 x1kg.",
])
def test_name_recovery_rejects_changed_content_or_ambiguous_occurrence(original):
    source = block("$749 " + original)
    offer = assemble(source)
    assert offer.raw_name is None
    assert "raw_name_invalid_reference" in offer.reason_codes
    assert offer.offer_price == Decimal("749")


def test_name_recovery_never_searches_other_fragments():
    source = block("$749 Sin nombre")
    source.fragments.append(EvidenceFragment(
        fragment_id="other", text="FAVORITA\nHarina 000 x1kg.",
        order=1, legibility="readable",
    ))
    assert assemble(source).raw_name is None


def test_partial_name_source_is_not_recovered():
    source = block("$749 FAVORITA\nHarina 000 x1kg.")
    source.fragments[0].legibility = "partial"
    assert assemble(source).raw_name is None


def test_whitespace_recovery_is_not_used_for_price_or_other_fields():
    source = block("$ 749 FAVORITA Harina 000 x1kg.")
    assert assemble(source).offer_price is None
    for field in ["brand", "price_type", "package_quantity", "minimum_purchase"]:
        ref = source.proposals[0].field_evidence[0].refs[0].model_copy(
            update={"quote": "FAVORITA\nHarina"}
        )
        assert StructuredProposalAssembler._reference(ref, field, source) is None


def test_colgate_recovered_name_is_usable_without_confirming_price_basis():
    text = ("COLGATE\nCrema dental\nTriple accion original x70g\n"
            "PRECIO XKG: $ 25.700,00\nDESDE 3 UNIDADES\n$3.699")
    source = block(text, quote="$3.699")
    name = "Crema dental Triple accion original x70g"
    source.proposals[0].raw_name = name
    source.proposals[0].offer_price = Decimal("3699")
    source.proposals[0].field_evidence[0].refs[0].quote = name
    offer = assemble(source)
    assert offer.raw_name == name
    assert offer.field_evidence["raw_name"][0].quote == (
        "Crema dental\nTriple accion original x70g"
    )
    assert usable_evidence(offer, source)
    result = DocumentDecisionPolicy().validate_decision(
        request=OfferDecisionRequest(
            business_id="tenant", block=source, offer=offer, candidates=[], questions=[]
        ), decision=None,
    )
    assert result.offer.price_status == "pending"
    assert result.offer.price_type == "unknown"
    assert "raw_name_invalid_reference" not in result.reason_codes
