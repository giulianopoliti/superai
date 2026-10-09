from decimal import Decimal

import pytest

from app.modules.procurement.analysis_contracts import (
    BlockClassificationResult,
    ChoiceDecision,
    EvidenceFragment,
    OfferDecisionRequest,
    OfferTextBlock,
)
from app.modules.procurement.block_interpretation import (
    DeterministicBlockAssembler,
    detect_value_candidates,
    price_occurrences,
    validate_value_candidates,
    value_question_id,
)
from app.modules.procurement.decision_policy import DocumentDecisionPolicy, comparable_unit_price
from app.providers.jev_analysis import prepare_block_request


def block(*texts, conditions=None):
    fragments = [
        EvidenceFragment(fragment_id=f"f{i}", text=text, order=i, legibility="readable")
        for i, text in enumerate(texts)
    ]
    return OfferTextBlock(
        block_id="b",
        run_id="run",
        reading_id="reading",
        page_number=1,
        order_on_page=0,
        full_text="\n".join(texts),
        fragments=fragments,
        page_conditions=conditions or [],
        condition_refs=[f.fragment_id for f in conditions or []],
    )


def decide(request, selections):
    decisions = []
    for question in request.questions:
        if question.question_id not in selections:
            continue
        choice = selections[question.question_id]
        assert choice in question.options
        decisions.append(
            ChoiceDecision(
                question_id=question.question_id,
                choice=choice,
                confidence=0.7,
                probabilities={option: int(option == choice) for option in question.options},
                model="jev-1.13.0",
                prompt_version="fixture",
            )
        )
    return BlockClassificationResult(
        run_id=request.block.run_id,
        block_id=request.block.block_id,
        decisions=decisions,
    )


def assemble(block, assignments):
    request = prepare_block_request(business_id="tenant", block=block)
    classification = decide(request, assignments(request))
    return DeterministicBlockAssembler().assemble_interpretation(
        business_id="tenant",
        block=block,
        candidates=request.candidates,
        classification=classification,
    )


def candidate(request, literal, kind):
    return next(c for c in request.candidates if c.literal == literal and c.kind == kind)


def fields(request, price, **selected):
    index = next(
        i
        for i, c in enumerate(price_occurrences(request.block, request.candidates))
        if c.candidate_id == price.candidate_id
    )
    choices = {
        "role": "current_price",
        "raw_name": "insufficient",
        "unit_size": "none",
        "package_quantity": "none",
        "retail_contents_count": "none",
        "minimum_purchase": "none",
        "minimum_scope": "unknown",
        "price_type": "unknown",
    }
    choices.update(selected)
    return {value_question_id(index, field): choice for field, choice in choices.items()}


def test_candidates_have_exact_offsets_local_parse_and_stable_ids():
    evidence = block("6 botellas de 1,5 L — $12.000 por bulto, mínimo 2 bultos")
    candidates = detect_value_candidates(block=evidence)
    for c in candidates:
        assert evidence.fragments[0].text[c.start : c.end] == c.literal
    assert next(c for c in candidates if c.literal == "1,5 L").value == Decimal("1.5")
    assert next(c for c in candidates if c.literal == "$12.000").value == Decimal("12000")
    assert next(c for c in candidates if c.literal == "2 bultos").unit == "bultos"
    assert candidates == detect_value_candidates(block=evidence)


@pytest.mark.parametrize(
    "change",
    [
        {"block_id": "foreign"},
        {"fragment_id": "foreign"},
        {"value": Decimal("9999")},
        {"literal": "altered"},
        {"start": 1},
        {"end": 9999},
        {"candidate_id": "none"},
    ],
)
def test_foreign_and_altered_candidates_rejected(change):
    evidence = block("Aceite — $100")
    candidates = detect_value_candidates(block=evidence)
    selected = next(c for c in candidates if c.kind == "money")
    with pytest.raises(ValueError):
        validate_value_candidates(block=evidence, candidates=[selected.model_copy(update=change)])


def test_duplicate_candidates_rejected():
    evidence = block("Aceite — $100")
    candidates = detect_value_candidates(block=evidence)
    with pytest.raises(ValueError):
        validate_value_candidates(block=evidence, candidates=candidates + candidates[:1])


def test_pack_size_contents_and_minimum_are_distinct_no_calculation():
    evidence = block("6 botellas de 1,5 L — $12.000 por bulto, mínimo 2 bultos")

    def assignments(request):
        return fields(
            request,
            candidate(request, "$12.000", "money"),
            raw_name=candidate(request, "6 botellas de 1,5 L", "text").candidate_id,
            unit_size=candidate(request, "1,5 L", "size").candidate_id,
            package_quantity=candidate(request, "6 botellas", "quantity").candidate_id,
            minimum_purchase=candidate(request, "2 bultos", "quantity").candidate_id,
            minimum_scope="offer",
            price_type="pack",
        )

    result = assemble(evidence, assignments)
    offer = result.offers[0]
    assert offer.offer_price == Decimal("12000")  # Never divides by 6, 2 or 1.5.
    assert offer.unit_size == Decimal("1.5") and offer.unit == "l"
    assert offer.package_quantity == 6
    assert offer.minimum_purchase.value == 2 and offer.minimum_purchase.unit == "bultos"
    assert offer.minimum_purchase.scope == "offer"
    assert offer.identity_status == offer.price_status == "pending"
    assert offer.currency == "$" and "currency_pending" in offer.reason_codes


def test_x48_is_retail_contents_and_minimum_does_not_make_pack():
    evidence = block("Toallitas x48u — $850 por envase, mínimo 6 unidades")

    def assignments(request):
        return fields(
            request,
            candidate(request, "$850", "money"),
            raw_name=candidate(request, "Toallitas x48u", "text").candidate_id,
            retail_contents_count=candidate(request, "48u", "quantity").candidate_id,
            minimum_purchase=candidate(request, "6 unidades", "quantity").candidate_id,
            minimum_scope="offer",
            price_type="unit",
        )

    offer = assemble(evidence, assignments).offers[0]
    assert offer.retail_contents_count == 48 and offer.package_quantity is None
    assert offer.price_type == "unit" and offer.minimum_purchase.value == 6
    assert offer.offer_price == 850


def test_previous_current_prices_and_variants_have_no_cartesian_product():
    evidence = block("Aceite A — antes $120 ahora $100", "Aceite B — $200")

    def assignments(request):
        first = fields(request, candidate(request, "$120", "money"), role="previous_price")
        first.update(
            fields(
                request,
                candidate(request, "$100", "money"),
                raw_name=candidate(request, "Aceite A", "text").candidate_id,
            )
        )
        first.update(
            fields(
                request,
                candidate(request, "$200", "money"),
                raw_name=candidate(request, "Aceite B", "text").candidate_id,
            )
        )
        return first

    offers = assemble(evidence, assignments).offers
    assert [(o.raw_name, o.offer_price) for o in offers] == [("Aceite A", 100), ("Aceite B", 200)]
    assert offers[0].field_evidence["previous_price"][0].quote == "$120"
    assert "previous_price" not in offers[1].field_evidence


def test_discount_cannot_become_absolute_price_even_if_jev_selects_it():
    evidence = block("10% por display")
    request = prepare_block_request(business_id="tenant", block=evidence)
    assert candidate(request, "10", "number")  # Original evidence remains auditable.
    assert not price_occurrences(evidence, request.candidates)
    assert not any(q.question_id.startswith("value:") for q in request.questions)
    result = assemble(evidence, lambda r: {})
    assert not result.offers
    assert evidence.full_text == "10% por display"


def test_size_and_contents_cannot_become_price():
    evidence = block("Toallitas x48u y botella 1,5 L")
    request = prepare_block_request(business_id="tenant", block=evidence)
    assert candidate(request, "48", "number")
    assert not price_occurrences(evidence, request.candidates)
    result = assemble(evidence, lambda r: {})
    assert not result.offers


def test_missing_answers_and_provider_failure_keep_pending_without_vision():
    evidence = block("Producto ilegible — $100")
    request = prepare_block_request(business_id="tenant", block=evidence)
    assembler = DeterministicBlockAssembler()
    result = assembler.assemble_interpretation(
        business_id="tenant",
        block=evidence,
        classification=None,
        candidates=request.candidates,
    )
    assert not result.offers and result.reason_codes == ["jev_unavailable"]
    assert not result.needs_visual_review
    partial = assemble(evidence, lambda r: fields(r, candidate(r, "$100", "money")))
    assert partial.offers[0].raw_name is None
    assert partial.offers[0].offer_price == 100
    assert not partial.needs_visual_review


def test_commercial_count_is_not_offered_as_retail_contents():
    evidence = block("Pack 6 unidades — $600")

    def assignments(request):
        return fields(
            request,
            candidate(request, "$600", "money"),
            package_quantity=candidate(request, "6 unidades", "quantity").candidate_id,
            retail_contents_count=candidate(request, "6", "number").candidate_id,
            price_type="pack",
        )

    offer = assemble(evidence, assignments).offers[0]
    assert offer.package_quantity == 6 and offer.retail_contents_count is None
    request = prepare_block_request(business_id="tenant", block=evidence)
    assert not any(q.question_id.endswith(":retail_contents_count") for q in request.questions)
    assert offer.price_status == "pending"


def test_classification_binding_and_page_condition_scope():
    evidence = block(
        "Aceite — $100",
        conditions=[
            EvidenceFragment(
                fragment_id="condition", text="Mínimo 2 bultos", order=0, legibility="readable"
            ),
        ],
    )
    request = prepare_block_request(business_id="tenant", block=evidence)
    wrong = BlockClassificationResult(run_id="other", block_id=evidence.block_id)
    with pytest.raises(ValueError):
        DeterministicBlockAssembler().assemble_interpretation(
            business_id="tenant",
            block=evidence,
            classification=wrong,
            candidates=request.candidates,
        )
    result = assemble(
        evidence,
        lambda r: fields(
            r,
            candidate(r, "$100", "money"),
            minimum_purchase=candidate(r, "2 bultos", "quantity").candidate_id,
            minimum_scope="page",
        ),
    )
    assert result.offers[0].minimum_purchase.scope == "page"
    assert result.offers[0].minimum_purchase.refs[0].fragment_id == "condition"


def test_unreadable_amount_is_absent_and_evidence_remains():
    evidence = block("Aceite — $100")
    evidence.fragments[0].legibility = "partial"
    result = assemble(evidence, lambda r: fields(r, candidate(r, "$100", "money")))
    assert result.offers[0].offer_price is None
    assert result.offers[0].field_evidence["offer_price"][0].quote == "$100"


@pytest.mark.parametrize(
    "literal,value",
    [
        ("$12.000", Decimal("12000")),
        ("$1.234,56", Decimal("1234.56")),
        ("$850,50", Decimal("850.50")),
    ],
)
def test_argentine_money_and_compact_size(literal, value):
    evidence = block(f"Aceite 1,5L — {literal}")
    values = detect_value_candidates(block=evidence)
    money = next(c for c in values if c.kind == "money")
    size = next(c for c in values if c.kind == "size")
    assert money.literal == literal and money.value == value
    assert size.literal == "1,5L" and size.value == Decimal("1.5") and size.unit == "l"


@pytest.mark.parametrize("basis", ["unit", "pack"])
def test_jev_cannot_impose_basis_without_explicit_text(basis):
    evidence = block("Toallitas x48u — $850, mínimo 6 unidades")
    result = assemble(
        evidence,
        lambda r: fields(
            r,
            candidate(r, "$850", "money"),
            price_type=basis,
        ),
    )
    assert result.offers[0].price_type == "unknown"
    assert result.offers[0].price_status == "pending"
    assert "price_basis_evidence_pending" in result.offers[0].reason_codes


def test_same_name_with_multiple_prices_remains_ambiguous():
    evidence = block("Aceite — $100 o $120")

    def assignments(request):
        choices = fields(
            request,
            candidate(request, "$100", "money"),
            raw_name=candidate(request, "Aceite", "text").candidate_id,
        )
        choices.update(
            fields(
                request,
                candidate(request, "$120", "money"),
                raw_name=candidate(request, "Aceite", "text").candidate_id,
            )
        )
        return choices

    result = assemble(evidence, assignments)
    assert len(result.offers) == 2
    assert "multiple_prices_same_name_pending" in result.reason_codes
    assert all(o.price_status == "pending" for o in result.offers)


@pytest.mark.parametrize("text,literal", [("Toallitas x48", "48"), ("Código 123", "123")])
def test_bare_contents_or_codes_cannot_be_prices(text, literal):
    evidence = block(text)
    request = prepare_block_request(business_id="tenant", block=evidence)
    assert candidate(request, literal, "number")
    assert not price_occurrences(evidence, request.candidates)
    result = assemble(evidence, lambda r: {})
    assert not result.offers


@pytest.mark.parametrize("separate_condition", [False, True])
@pytest.mark.parametrize("count_kind,count_literal", [("number", "6"), ("quantity", "pack x6")])
def test_b_assembler_resolves_separate_basis_and_pack_context_with_real_policy(
    separate_condition,
    count_kind,
    count_literal,
):
    texts = ["Aceite 1,5L", "pack x6", "$12.000"]
    if separate_condition:
        basis_id = "condition"
        evidence = block(
            *texts,
            conditions=[
                EvidenceFragment(
                    fragment_id=basis_id,
                    text="Precio por bulto",
                    order=0,
                    legibility="readable",
                )
            ],
        )
    else:
        evidence = block(*texts, "Precio por bulto")
        basis_id = evidence.fragments[-1].fragment_id

    def assignments(request):
        choices = {
            q.question_id: "not_price" for q in request.questions if q.question_id.endswith(":role")
        }
        choices.update(
            fields(
                request,
                candidate(request, "$12.000", "money"),
                raw_name=candidate(request, "Aceite 1,5L", "text").candidate_id,
                unit_size=candidate(request, "1,5L", "size").candidate_id,
                package_quantity=candidate(request, count_literal, count_kind).candidate_id,
                price_type="pack",
                basis_evidence=basis_id,
            )
        )
        return choices

    result = assemble(evidence, assignments)
    offer = result.offers[0]
    assert offer.field_evidence["package_quantity"][0].quote == "pack x6"
    assert basis_id in {r.fragment_id for r in offer.field_evidence["price_type"]}
    policy = DocumentDecisionPolicy().validate_decision(
        request=OfferDecisionRequest(
            business_id="tenant", block=evidence, offer=offer, candidates=[], questions=[]
        ),
        decision=None,
    )
    assert offer.price_status == "pending"  # Assembly never confirms.
    assert policy.offer.price_status == "confirmed"
    assert comparable_unit_price(offer=policy.offer, block=evidence) == Decimal("2000.00")


def test_b_unit_basis_on_page_condition_with_contents_and_minimum_real_policy():
    evidence = block(
        "Toallitas x48u",
        "$850",
        "Mínimo 6 unidades",
        conditions=[
            EvidenceFragment(
                fragment_id="condition", text="Precio por envase", order=0, legibility="readable"
            ),
        ],
    )

    def assignments(request):
        choices = {
            q.question_id: "not_price" for q in request.questions if q.question_id.endswith(":role")
        }
        choices.update(
            fields(
                request,
                candidate(request, "$850", "money"),
                raw_name=candidate(request, "Toallitas x48u", "text").candidate_id,
                retail_contents_count=candidate(request, "48u", "quantity").candidate_id,
                minimum_purchase=candidate(request, "6 unidades", "quantity").candidate_id,
                minimum_scope="offer",
                price_type="unit",
                basis_evidence="condition",
            )
        )
        return choices

    offer = assemble(evidence, assignments).offers[0]
    policy = DocumentDecisionPolicy().validate_decision(
        request=OfferDecisionRequest(
            business_id="tenant", block=evidence, offer=offer, candidates=[], questions=[]
        ),
        decision=None,
    )
    assert policy.offer.price_status == "confirmed"
    assert comparable_unit_price(offer=policy.offer, block=evidence) == Decimal("850.00")
    assert policy.offer.package_quantity is None
    assert policy.offer.retail_contents_count == 48
    assert policy.offer.minimum_purchase.value == 6


def test_contradictory_bases_or_minimum_selected_as_pack_remain_pending_real_policy():
    for text in ("6 botellas — $600 por unidad", "Mínimo 6 botellas — $600"):
        evidence = block(text, "Precio por bulto")
        offer = assemble(
            evidence,
            lambda r: fields(
                r,
                candidate(r, "$600", "money"),
                package_quantity=candidate(r, "6 botellas", "quantity").candidate_id,
                price_type="pack",
                basis_evidence="f1",
            ),
        ).offers[0]
        result = DocumentDecisionPolicy().validate_decision(
            request=OfferDecisionRequest(
                business_id="tenant", block=evidence, offer=offer, candidates=[], questions=[]
            ),
            decision=None,
        )
        assert result.offer.price_status == "pending"
        assert comparable_unit_price(offer=result.offer, block=evidence) is None


def test_current_marker_is_exact_context_for_real_policy_previous_current_price():
    evidence = block("Aceite — antes $120 ahora $100 por unidad")

    def assignments(request):
        choices = fields(request, candidate(request, "$120", "money"), role="previous_price")
        choices.update(fields(request, candidate(request, "$100", "money"), price_type="unit"))
        return choices

    offer = assemble(evidence, assignments).offers[0]
    assert offer.field_evidence["offer_price"][0].quote == "ahora $100"
    result = DocumentDecisionPolicy().validate_decision(
        request=OfferDecisionRequest(
            business_id="tenant", block=evidence, offer=offer, candidates=[], questions=[]
        ),
        decision=None,
    )
    assert result.offer.price_status == "confirmed"
    assert comparable_unit_price(offer=result.offer, block=evidence) == Decimal("100.00")


def test_vital_natura_multiline_name_after_amount_is_selectable_and_counts_are_typed():
    text = "DESDE 3 UNIDADES\n$3.649\nNATURA\nAceite girasol\nx900ml.\nPRECIO XLT: $4054.44"
    evidence = block(text)
    request = prepare_block_request(business_id="tenant", block=evidence)
    name = candidate(request, "NATURA\nAceite girasol\nx900ml.", "text")
    assert text[name.start : name.end] == name.literal
    assert not any(c.literal == "DESDE 3 UNIDADES" and c.kind == "text" for c in request.candidates)
    assert {c.literal for c in price_occurrences(evidence, request.candidates)} == {
        "$3.649",
        "$4054.44",
    }
    count_options = {
        option
        for q in request.questions
        if q.question_id.endswith((":package_quantity", ":retail_contents_count"))
        for option in q.options
    }
    size_ids = {c.candidate_id for c in request.candidates if c.value in {3, 900}}
    assert not count_options & size_ids

    def assignments(r):
        choices = fields(
            r,
            candidate(r, "$3.649", "money"),
            raw_name=name.candidate_id,
            unit_size=candidate(r, "900ml", "size").candidate_id,
            minimum_purchase=candidate(r, "3 UNIDADES", "quantity").candidate_id,
            minimum_scope="offer",
        )
        # Even a bad JEV role for the explicit XLT cannot create a second offer.
        choices.update(fields(r, candidate(r, "$4054.44", "money"), role="current_price"))
        return choices

    result = assemble(evidence, assignments)
    assert len(result.offers) == 1
    offer = result.offers[0]
    assert offer.raw_name == name.literal
    assert offer.offer_price == Decimal("3649")
    assert offer.unit_size == 900 and offer.unit == "ml"
    assert offer.minimum_purchase.value == 3
    assert offer.package_quantity is None and offer.retail_contents_count is None
    assert "reference_price" in offer.field_evidence


def test_vital_heineken_cc_and_pack_condition_do_not_supply_commercial_count():
    evidence = block(
        "LLEVANDO POR PACK\n$4.489\nHEINEKEN\nCerveza lata\nx710cc.\nPRECIO XLT: $6322.54"
    )

    def assignments(r):
        return fields(
            r,
            candidate(r, "$4.489", "money"),
            raw_name=candidate(r, "HEINEKEN\nCerveza lata\nx710cc.", "text").candidate_id,
            unit_size=candidate(r, "710cc", "size").candidate_id,
            price_type="pack",
            basis_evidence="f0",
        )

    offer = assemble(evidence, assignments).offers[0]
    assert offer.unit_size == 710 and offer.unit == "ml"
    assert offer.package_quantity is None
    result = DocumentDecisionPolicy().validate_decision(
        request=OfferDecisionRequest(
            business_id="tenant",
            block=evidence,
            offer=offer,
            candidates=[],
            questions=[],
        ),
        decision=None,
    )
    assert result.offer.price_status == "pending"
    assert comparable_unit_price(offer=result.offer, block=evidence) is None


def test_vital_kimbies_minimum_and_retail_contents_are_not_pack_options():
    evidence = block("DESDE 2 UNIDADES\n$1.869\nKIMBIES\nToalla humeda\nx48u\nPRECIO XU: $38,94")
    request = prepare_block_request(business_id="tenant", block=evidence)
    assert not any(q.question_id.endswith(":package_quantity") for q in request.questions)
    content = candidate(request, "48u", "quantity")
    minimum = candidate(request, "2 UNIDADES", "quantity")
    assert any(
        content.candidate_id in q.options
        for q in request.questions
        if q.question_id.endswith(":retail_contents_count")
    )
    assert any(
        minimum.candidate_id in q.options
        for q in request.questions
        if q.question_id.endswith(":minimum_purchase")
    )


def test_reference_evidence_alone_does_not_mark_valid_offer_as_pending():
    evidence = block("KEVIN\nDesodorante corporal dubal\nx150ml\nPRECIO XLT: $25.926,67\n$3.889")

    def assignments(r):
        choices = fields(
            r,
            candidate(r, "$3.889", "money"),
            raw_name=candidate(r, "KEVIN\nDesodorante corporal dubal\nx150ml", "text").candidate_id,
            unit_size=candidate(r, "150ml", "size").candidate_id,
        )
        choices.update(fields(r, candidate(r, "$25.926,67", "money"), role="reference_price"))
        return choices

    result = assemble(evidence, assignments)
    assert len(result.offers) == 1
    assert result.offers[0].offer_price == 3889
    assert result.offers[0].field_evidence["reference_price"]
    assert "reference_price_evidence_retained" not in result.reason_codes
