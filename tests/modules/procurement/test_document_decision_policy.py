from decimal import Decimal

import pytest

from app.modules.procurement.analysis_contracts import (
    ChoiceDecision,
    ChoiceQuestion,
    EvidenceFragment,
    EvidenceRef,
    IdentityCandidate,
    MinimumPurchase,
    OfferDecisionRequest,
    OfferDecisionResult,
    OfferInterpretation,
    OfferTextBlock,
)
from app.modules.procurement.decision_policy import (
    DecisionThresholds,
    DocumentDecisionPolicy,
    build_offer_decision_request,
    comparable_unit_price,
    evidence_text,
    observed_offer_price,
    published_price_evidence_valid,
)


def scenario(*, basis="pack", quantity=6, confirmed_identity=True):
    texts = [
        "Gaseosa original 1,5 L",
        "$12.000",
        "Precio por bulto",
        "6 botellas",
        "Minimo 2 bultos",
    ]
    fragments = [
        EvidenceFragment(
            fragment_id=f"f{i}",
            text=text,
            order=i,
            legibility="readable",
        )
        for i, text in enumerate(texts)
    ]
    block = OfferTextBlock(
        block_id="block",
        run_id="run",
        reading_id="reading",
        page_number=1,
        order_on_page=0,
        full_text="\n".join(texts),
        fragments=fragments,
    )
    fields = {
        "raw_name": [EvidenceRef(fragment_id="f0", quote=texts[0])],
        "offer_price": [EvidenceRef(fragment_id="f1", quote=texts[1])],
        "price_type": [EvidenceRef(fragment_id="f2", quote=texts[2])],
        "package_quantity": [EvidenceRef(fragment_id="f3", quote=texts[3])],
    }
    offer = OfferInterpretation(
        offer_id="offer",
        block_id="block",
        business_id="b",
        raw_name=texts[0],
        offer_price=Decimal("12000"),
        price_type=basis,
        package_quantity=quantity,
        unit_size=Decimal("1.5"),
        unit="l",
        field_evidence=fields,
        minimum_purchase=MinimumPurchase(
            value=2,
            unit="bulto",
            scope="offer",
            refs=[EvidenceRef(fragment_id="f4", quote=texts[4])],
        ),
    )
    candidate = IdentityCandidate(
        candidate_id="candidate",
        product_id="product",
        business_id="b",
        name=texts[0],
        unit_size=Decimal("1.5"),
        unit="l",
        reason_codes=["auto_accepted_identity_v1"] if confirmed_identity else [],
    )
    return OfferDecisionRequest(
        business_id="b", block=block, offer=offer, candidates=[candidate], questions=[]
    )


def answer(request, purpose, choice, options, *, probability=0.99):
    question_id = f"{request.offer.offer_id}:{purpose}"
    request.questions.append(
        ChoiceQuestion(
            question_id=question_id,
            options=options,
            prompt="test",
        )
    )
    probabilities = {key: (1 - probability) / (len(options) - 1) for key in options}
    probabilities[choice] = probability
    return OfferDecisionResult(
        offer_id=request.offer.offer_id,
        decisions=[
            ChoiceDecision(
                question_id=question_id,
                choice=choice,
                confidence=0.99,
                probabilities=probabilities,
                model="fake",
                prompt_version="test",
            )
        ],
    )


def test_pack_six_normalizes_decimal_and_never_uses_minimum_two():
    request = scenario()
    original = request.model_dump()
    result = DocumentDecisionPolicy().validate_decision(request=request, decision=None)
    assert result.route == "resolved"
    assert result.offer.identity_status == result.offer.price_status == "confirmed"
    assert comparable_unit_price(offer=result.offer, block=request.block) == Decimal("2000.00")
    assert result.offer.offer_price == Decimal("12000")
    assert result.offer.minimum_purchase.value == 2
    assert request.model_dump() == original


def test_identity_resolved_price_pending_and_inverse():
    request = scenario(quantity=None)
    result = DocumentDecisionPolicy().validate_decision(request=request, decision=None)
    assert result.offer.identity_status == "confirmed"
    assert result.offer.price_status == "pending"
    assert comparable_unit_price(offer=result.offer, block=request.block) is None
    request = scenario(confirmed_identity=False)
    result = DocumentDecisionPolicy().validate_decision(request=request, decision=None)
    assert result.offer.identity_status == "pending"
    assert result.offer.price_status == "confirmed"
    assert result.route == "needs_jev"


@pytest.mark.parametrize(
    "text,quantity",
    [
        ("Toallitas x48", 48),
        ("Minimo 2 bultos", 2),
        ("Contenido 6 unidades", 6),
        ("6 botellas", None),
        ("6 botellas", 12),
        ("x6", 6),
    ],
)
def test_contents_minimum_bare_regex_and_missing_quantity_never_divide(text, quantity):
    request = scenario(quantity=quantity)
    request.block.fragments[3].text = text
    request.block.full_text = "\n".join(f.text for f in request.block.fragments)
    request.offer.field_evidence["package_quantity"] = [EvidenceRef(fragment_id="f3", quote=text)]
    result = DocumentDecisionPolicy().validate_decision(request=request, decision=None)
    assert result.offer.price_status == "pending"
    assert comparable_unit_price(offer=result.offer, block=request.block) is None


def test_shared_contents_reference_not_used_for_commercial_quantity():
    request = scenario()
    request.offer.field_evidence["retail_contents_count"] = request.offer.field_evidence[
        "package_quantity"
    ]
    result = DocumentDecisionPolicy().validate_decision(request=request, decision=None)
    assert result.offer.price_status == "pending"


def test_contents_48_do_not_change_explicit_unit_price():
    request = scenario(basis="unit", quantity=None)
    request.block.fragments[2].text = "Precio por unidad"
    request.block.full_text = "\n".join(f.text for f in request.block.fragments)
    request.offer.field_evidence["price_type"] = [
        EvidenceRef(
            fragment_id="f2",
            quote="Precio por unidad",
        )
    ]
    request.offer.retail_contents_count = 48
    result = DocumentDecisionPolicy().validate_decision(request=request, decision=None)
    assert comparable_unit_price(offer=result.offer, block=request.block) == Decimal("12000.00")


@pytest.mark.parametrize(
    "choice,code", [("none", "identity_none"), ("insufficient", "identity_insufficient")]
)
def test_abstentions_separate_without_semantic_visual_recovery(choice, code):
    request = scenario(confirmed_identity=False)
    decision = answer(request, "identity", choice, ["candidate", "none", "insufficient"])
    result = DocumentDecisionPolicy().validate_decision(request=request, decision=decision)
    assert code in result.reason_codes
    assert result.offer.identity_status == ("no_match" if choice == "none" else "pending")
    assert result.offer.price_status == "confirmed"
    assert result.route == ("resolved" if choice == "none" else "needs_human_review")
    assert not result.offer.needs_visual_review


def test_provider_error_is_not_none_or_insufficient_or_visual():
    request = scenario(confirmed_identity=False)
    result = DocumentDecisionPolicy().validate_decision(
        request=request,
        decision=OfferDecisionResult(offer_id="offer", reason_codes=["timeout"]),
    )
    assert "jev_provider_error" in result.reason_codes
    assert result.offer.identity_status == "pending"
    assert result.offer.price_status == "confirmed"
    assert result.route == "needs_human_review"


def test_high_probability_alone_cannot_confirm_or_divide():
    request = scenario(basis="unknown", confirmed_identity=False)
    request.offer.field_evidence.pop("price_type")
    decision = answer(request, "price_basis", "pack", ["unit", "pack", "unknown"])
    policy = DocumentDecisionPolicy(thresholds=DecisionThresholds(allow_jev_acceptance=True))
    result = policy.validate_decision(request=request, decision=decision)
    assert result.offer.price_status == "pending"
    assert comparable_unit_price(offer=result.offer, block=request.block) is None


def test_identity_jev_acceptance_enabled_by_default_requires_evidence_and_can_be_disabled():
    request = scenario(confirmed_identity=False)
    decision = answer(request, "identity", "candidate", ["candidate", "none", "insufficient"])
    disabled = DocumentDecisionPolicy(thresholds=DecisionThresholds(allow_jev_acceptance=False))
    result = disabled.validate_decision(request=request, decision=decision)
    assert result.offer.identity_status == "pending"
    policy = DocumentDecisionPolicy()
    result = policy.validate_decision(request=request, decision=decision)
    assert result.offer.identity_status == "confirmed"
    request.offer.field_evidence.pop("raw_name")
    result = policy.validate_decision(request=request, decision=decision)
    assert result.offer.identity_status == "pending"


@pytest.mark.parametrize("change", ["foreign", "unreferenced_condition", "offset", "quote"])
def test_invalid_refs_never_confirm_price(change):
    request = scenario()
    ref = request.offer.field_evidence["offer_price"][0]
    if change == "foreign":
        ref.fragment_id = "another-block-fragment"
    elif change == "unreferenced_condition":
        request.block.page_conditions = [
            EvidenceFragment(
                fragment_id="condition",
                text="$12.000",
                order=0,
                legibility="readable",
            )
        ]
        ref.fragment_id = "condition"
    elif change == "offset":
        ref.start, ref.end = 1, 2
    else:
        ref.quote = "$99.999"
    result = DocumentDecisionPolicy().validate_decision(request=request, decision=None)
    assert result.offer.price_status == "pending"


def test_explicit_same_page_condition_can_support_basis():
    request = scenario()
    request.block.page_conditions = [
        EvidenceFragment(
            fragment_id="condition",
            text="Precio por bulto",
            order=0,
            legibility="readable",
        )
    ]
    request.block.condition_refs = ["condition"]
    request.offer.field_evidence["price_type"] = [
        EvidenceRef(
            fragment_id="condition",
            quote="Precio por bulto",
        )
    ]
    assert (
        DocumentDecisionPolicy()
        .validate_decision(
            request=request,
            decision=None,
        )
        .offer.price_status
        == "confirmed"
    )


@pytest.mark.parametrize(
    "text,quote,price,confirmed",
    [
        ("Antes $15.000", "$15.000", "15000", False),
        ("Antes $15.000 ahora $12.000", "ahora $12.000", "12000", True),
        ("Antes $15.000 ahora $12.000", "Antes $15.000 ahora $12.000", "12000", False),
        ("20% descuento", "20% descuento", "20", False),
        ("$12.000", "$12.000", "9999", False),
    ],
)
def test_current_previous_promotion_and_no_generated_numbers(text, quote, price, confirmed):
    request = scenario()
    request.block.fragments[1].text = text
    request.block.full_text = "\n".join(f.text for f in request.block.fragments)
    request.offer.field_evidence["offer_price"] = [EvidenceRef(fragment_id="f1", quote=quote)]
    request.offer.offer_price = Decimal(price)
    result = DocumentDecisionPolicy().validate_decision(request=request, decision=None)
    assert (result.offer.price_status == "confirmed") == confirmed


def test_visual_recovery_is_explicit_and_exhaustion_stays_human():
    request = scenario(quantity=None)
    request.block.fragments[3].legibility = "unreadable"
    result = DocumentDecisionPolicy().validate_decision(request=request, decision=None)
    assert result.route == "needs_visual_review"
    request.offer.reason_codes.append("visual_attempt_exhausted")
    result = DocumentDecisionPolicy().validate_decision(request=request, decision=None)
    assert result.route == "needs_human_review"
    assert not result.offer.needs_visual_review


def test_feedback_conflict_not_overridden_by_jev():
    request = scenario()
    request.candidates[0].reason_codes = ["confirmed_by_feedback", "presentation_conflict"]
    decision = answer(request, "identity", "candidate", ["candidate", "none", "insufficient"])
    policy = DocumentDecisionPolicy(thresholds=DecisionThresholds(allow_jev_acceptance=True))
    result = policy.validate_decision(request=request, decision=decision)
    assert result.offer.identity_status == "pending"
    assert "human_feedback_conflict" in result.reason_codes


def test_wrong_offer_and_question_option_sets_do_not_corrupt_clear_price():
    request = scenario(confirmed_identity=False)
    decision = answer(request, "identity", "candidate", ["candidate", "none", "insufficient"])
    decision.offer_id = "other-offer"
    result = DocumentDecisionPolicy().validate_decision(request=request, decision=decision)
    assert "jev_invalid_response" in result.reason_codes
    assert result.offer.price_status == "confirmed"
    assert result.offer.identity_status == "pending"


def test_nullable_promotion_kept_without_fictitious_price_or_name():
    request = scenario()
    request.offer.raw_name, request.offer.offer_price = None, None
    request.offer.field_evidence = {}
    request.candidates = []
    result = DocumentDecisionPolicy().validate_decision(request=request, decision=None)
    assert result.offer.offer_price is None
    assert result.offer.offer_id == "offer"
    assert result.route == "needs_human_review"


def test_offset_quote_pair_and_threshold_validation():
    request = scenario()
    assert (
        evidence_text(
            request.block,
            [
                EvidenceRef(
                    fragment_id="f1",
                    quote="$12.000",
                    start=0,
                    end=7,
                )
            ],
        )
        == "$12.000"
    )
    for value in [float("nan"), float("inf"), -0.1, 1.1]:
        with pytest.raises(ValueError):
            DecisionThresholds(margin=value)


def test_prepare_questions_only_for_unresolved_dimensions():
    policy = DocumentDecisionPolicy()
    assert policy.prepare_request(request=scenario()).questions == []
    request = scenario(confirmed_identity=False)
    prepared = policy.prepare_request(request=request)
    assert [q.question_id for q in prepared.questions] == ["offer:identity"]
    assert prepared.questions[0].options == ["candidate", "none", "insufficient"]
    assert request.questions == []
    # Missing quantity with explicit pack basis cannot be fixed by choosing a basis again.
    assert policy.prepare_request(request=scenario(quantity=None)).questions == []


def test_foreign_tenant_variants_and_duplicate_candidates_never_confirm_identity():
    for change in ["tenant", "variant", "duplicate"]:
        request = scenario()
        if change == "tenant":
            request.candidates[0].business_id = "foreign"
        elif change == "variant":
            request.candidates[0].name = "Gaseosa zero 1,5 L"
        else:
            request.candidates.append(request.candidates[0].model_copy())
        result = DocumentDecisionPolicy().validate_decision(request=request, decision=None)
        assert result.offer.identity_status == "pending"
        assert result.offer.product_id is None


def test_low_probability_does_not_trigger_visual_review():
    request = scenario(confirmed_identity=False)
    decision = answer(
        request, "identity", "candidate", ["candidate", "none", "insufficient"], probability=0.6
    )
    policy = DocumentDecisionPolicy(thresholds=DecisionThresholds(allow_jev_acceptance=True))
    result = policy.validate_decision(request=request, decision=decision)
    assert result.route == "needs_human_review"
    assert result.offer.identity_status == "pending"


def test_fractional_quantities_and_overlapping_minimum_spans_never_divide():
    request = scenario(quantity=5)
    request.block.fragments[3].text = "6,5 botellas"
    request.block.full_text = "\n".join(f.text for f in request.block.fragments)
    request.offer.field_evidence["package_quantity"] = [
        EvidenceRef(
            fragment_id="f3",
            quote="6,5 botellas",
        )
    ]
    assert (
        DocumentDecisionPolicy()
        .validate_decision(
            request=request,
            decision=None,
        )
        .offer.price_status
        == "pending"
    )
    request = scenario()
    request.offer.minimum_purchase.refs = [
        EvidenceRef(
            fragment_id="f3",
            quote="6",
            start=0,
            end=1,
        )
    ]
    assert (
        DocumentDecisionPolicy()
        .validate_decision(
            request=request,
            decision=None,
        )
        .offer.price_status
        == "pending"
    )


def test_pending_price_output_is_unknown_without_mutating_original_pack_proposal():
    request = scenario(quantity=None)
    result = DocumentDecisionPolicy().validate_decision(request=request, decision=None)
    assert result.offer.price_type == "unknown"
    assert request.offer.price_type == "pack"


def test_builder_emits_integrable_shared_request_with_only_pending_identity():
    request = scenario(confirmed_identity=False)
    built = build_offer_decision_request(
        business_id="b",
        block=request.block,
        offer=request.offer,
        candidates=request.candidates,
    )
    assert isinstance(built, OfferDecisionRequest)
    assert [q.question_id for q in built.questions] == ["offer:identity"]
    assert request.questions == []


def test_partial_decision_preserves_price_and_reports_missing_question():
    request = scenario(confirmed_identity=False)
    request.questions = [
        ChoiceQuestion(
            question_id="offer:identity",
            options=["candidate", "none", "insufficient"],
            prompt="test",
        )
    ]
    result = DocumentDecisionPolicy().validate_decision(
        request=request,
        decision=OfferDecisionResult(offer_id="offer"),
    )
    assert "jev_missing_identity" in result.reason_codes
    assert result.offer.price_status == "confirmed"


@pytest.mark.parametrize("price,text", [("999", "$1.000"), ("100", "Antes $100")])
def test_unobserved_or_previous_price_becomes_nullable_but_proposal_survives(price, text):
    request = scenario()
    request.block.fragments[1].text = text
    request.block.full_text = "\n".join(f.text for f in request.block.fragments)
    request.offer.offer_price = Decimal(price)
    request.offer.field_evidence["offer_price"] = [EvidenceRef(fragment_id="f1", quote=text)]
    result = DocumentDecisionPolicy().validate_decision(request=request, decision=None)
    assert result.offer.offer_price is None
    assert result.offer.price_type == "unknown"
    assert result.offer.price_status == "pending"
    assert request.offer.offer_price == Decimal(price)
    assert "published_price_unverified" in result.reason_codes


def test_invented_name_and_presentation_become_nullable_not_false_confirmations():
    request = scenario()
    request.offer.raw_name = "Producto inventado 3 L"
    request.offer.unit_size = Decimal("3")
    result = DocumentDecisionPolicy().validate_decision(request=request, decision=None)
    assert result.offer.raw_name is None
    assert result.offer.unit_size is None
    assert result.offer.identity_status == "pending"
    assert result.offer.offer_price == Decimal("12000")
    assert request.offer.raw_name == "Producto inventado 3 L"


def test_valid_observed_price_with_missing_basis_keeps_price_and_unknown():
    request = scenario()
    request.offer.field_evidence.pop("price_type")
    result = DocumentDecisionPolicy().validate_decision(request=request, decision=None)
    assert result.offer.offer_price == Decimal("12000")
    assert result.offer.price_type == "unknown"
    assert result.offer.price_status == "pending"


def test_verified_pack_suffix_resolves_identity_but_unknown_contents_do_not():
    request = scenario(confirmed_identity=False)
    request.block.fragments[0].text += " X6"
    request.block.full_text = "\n".join(f.text for f in request.block.fragments)
    request.offer.raw_name += " X6"
    request.offer.field_evidence["raw_name"][0].quote = request.offer.raw_name
    result = DocumentDecisionPolicy().validate_decision(request=request, decision=None)
    assert result.offer.identity_status == "confirmed"
    assert result.offer.price_status == "confirmed"
    request.offer.field_evidence.pop("package_quantity")
    result = DocumentDecisionPolicy().validate_decision(request=request, decision=None)
    assert result.offer.identity_status == "pending"


def test_human_alternative_remains_relationship_not_exact_identity():
    request = scenario()
    request.candidates[0].reason_codes = ["confirmed_by_feedback", "human_accepted_alternative"]
    result = DocumentDecisionPolicy().validate_decision(request=request, decision=None)
    assert result.offer.identity_status == "pending"
    assert result.offer.price_status == "confirmed"
    assert "human_accepted_alternative" in result.reason_codes
    assert "human_feedback_conflict" not in result.reason_codes


def test_real_b_assembler_fake_jev_exact_current_span_then_policy():
    from app.modules.procurement.analysis_contracts import BlockClassificationResult
    from app.modules.procurement.block_interpretation import (
        DeterministicBlockAssembler,
        classification_option_sets,
        detect_value_candidates,
        price_occurrences,
        value_question_id,
    )

    request = scenario(confirmed_identity=False)
    request.block.fragments[1].text = "Antes $100 ahora $90 por unidad"
    request.block.full_text = "\n".join(f.text for f in request.block.fragments)
    candidates = detect_value_candidates(block=request.block)
    option_sets = classification_option_sets(request.block, candidates)
    name = next(c for c in candidates if c.fragment_id == "f0" and c.kind == "text")
    size = next(c for c in candidates if c.fragment_id == "f0" and c.kind == "size")
    decisions = []

    def fake(question_id, choice):
        if question_id not in option_sets:
            # Fields without literal candidates are absent, rather than a paid
            # question whose only useful answer is 'none'.
            assert choice == "none"
            return
        decisions.append(
            ChoiceDecision(
                question_id=question_id,
                choice=choice,
                confidence=0.99,
                probabilities={
                    option: float(option == choice) for option in option_sets[question_id]
                },
                model="fake",
                prompt_version="test",
            )
        )

    for index, candidate in enumerate(price_occurrences(request.block, candidates)):
        role = (
            "current_price"
            if candidate.literal == "$90"
            else ("previous_price" if candidate.literal == "$100" else "not_price")
        )
        fake(value_question_id(index, "role"), role)
        if role == "current_price":
            for field, choice in {
                "raw_name": name.candidate_id,
                "unit_size": size.candidate_id,
                "package_quantity": "none",
                "retail_contents_count": "none",
                "minimum_purchase": "none",
                "price_type": "unit",
            }.items():
                fake(value_question_id(index, field), choice)
    classification = BlockClassificationResult(
        run_id="run",
        block_id="block",
        decisions=decisions,
    )
    assembled = DeterministicBlockAssembler().assemble_interpretation(
        business_id="b",
        block=request.block,
        classification=classification,
        candidates=candidates,
    )
    assert len(assembled.offers) == 1
    interpretation = assembled.offers[0]
    price_ref = interpretation.field_evidence["offer_price"][0]
    assert "$90" in price_ref.quote
    assert request.block.fragments[1].text[price_ref.start : price_ref.end] == price_ref.quote
    for narrow in [False, True]:
        selected = interpretation.model_copy(deep=True)
        if narrow:
            start = request.block.fragments[1].text.index("$90")
            selected.field_evidence["offer_price"] = [
                EvidenceRef(
                    fragment_id="f1",
                    quote="$90",
                    start=start,
                    end=start + 3,
                )
            ]
        result = DocumentDecisionPolicy().validate_decision(
            request=OfferDecisionRequest(
                business_id="b",
                block=request.block,
                offer=selected,
                candidates=[],
                questions=[],
            ),
            decision=None,
        )
        assert result.offer.price_status == "confirmed"
        assert comparable_unit_price(offer=result.offer, block=request.block) == Decimal("90.00")


def test_loreal_after_amount_is_not_the_unit_liter():
    request = scenario()
    text = "$8.499 L'OREAL ELVIVE máscara hyalur pure x300g"
    request.block.fragments[1].text = text
    request.block.full_text = "\n".join(f.text for f in request.block.fragments)
    request.offer.offer_price = Decimal("8499")
    request.offer.field_evidence["offer_price"] = [
        EvidenceRef(
            fragment_id="f1",
            quote="$8.499",
            start=0,
            end=6,
        )
    ]
    assert observed_offer_price(offer=request.offer, block=request.block) == Decimal("8499")


@pytest.mark.parametrize(
    "label", ["PRECIO XLT: $12.000", "PRECIO XKG: $12.000", "PRECIO XU: $12.000"]
)
def test_reference_amount_cannot_become_published_price_by_narrow_quote(label):
    request = scenario()
    request.block.fragments[1].text = label
    request.block.full_text = "\n".join(f.text for f in request.block.fragments)
    start = label.index("$12.000")
    request.offer.field_evidence["offer_price"] = [
        EvidenceRef(
            fragment_id="f1",
            quote="$12.000",
            start=start,
            end=start + 7,
        )
    ]
    assert observed_offer_price(offer=request.offer, block=request.block) is None


@pytest.mark.parametrize("text", ["Precio por envase", "Precio por sobre", "Precio por unidad"])
def test_explicit_unit_basis_expressions_from_reader_contract(text):
    request = scenario(basis="unit", quantity=None)
    request.block.fragments[2].text = text
    request.block.full_text = "\n".join(f.text for f in request.block.fragments)
    request.offer.field_evidence["price_type"] = [EvidenceRef(fragment_id="f2", quote=text)]
    result = DocumentDecisionPolicy().validate_decision(request=request, decision=None)
    assert result.offer.price_status == "confirmed"


def test_llevando_por_pack_does_not_confirm_total_even_with_narrow_citation():
    request = scenario()
    text = "LLEVANDO POR PACK"
    request.block.fragments[2].text = text
    request.block.full_text = "\n".join(f.text for f in request.block.fragments)
    request.offer.field_evidence["price_type"] = [
        EvidenceRef(
            fragment_id="f2",
            quote="POR PACK",
            start=9,
            end=17,
        )
    ]
    result = DocumentDecisionPolicy().validate_decision(request=request, decision=None)
    assert result.offer.identity_status == "confirmed"
    assert result.offer.price_status == "pending"
    assert "price_basis_unverified" in result.reason_codes
    assert comparable_unit_price(offer=result.offer, block=request.block) is None


def test_unit_price_explicit_inside_pack_purchase_condition_stays_unit():
    request = scenario(basis="unit", quantity=None)
    text = "LLEVANDO POR PACK. Precio por envase"
    request.block.fragments[2].text = text
    request.block.full_text = "\n".join(f.text for f in request.block.fragments)
    request.offer.field_evidence["price_type"] = [EvidenceRef(fragment_id="f2", quote=text)]
    result = DocumentDecisionPolicy().validate_decision(request=request, decision=None)
    assert result.offer.price_status == "confirmed"
    assert result.offer.price_type == "unit"
    assert comparable_unit_price(offer=result.offer, block=request.block) == Decimal("12000.00")


@pytest.mark.parametrize(
    "text,quote,amount",
    [
        ("Arroz 10% descuento por unidad", "10", "10"),
        ("Arroz 1kg por unidad", "1", "1"),
        ("Compra minimo 6 por unidad", "6", "6"),
        ("Compra minimo 6 botellas por unidad", "6 botellas", "6"),
        ("Contenido 48 unidades por unidad", "48", "48"),
        ("20 de descuento por unidad", "20", "20"),
        ("Precio $1000 por unidad", "100", "100"),
        ("Precio $1.000 por unidad", "000", "0"),
        ("Descuento $20 por unidad", "$20", "20"),
        ("Toallitas x48 por unidad", "48", "48"),
        ("Arroz 2.25L por unidad", "2", "2"),
    ],
)
def test_narrow_numeric_citations_cannot_hide_original_non_price_context(text, quote, amount):
    request = scenario()
    request.block.fragments[1].text = text
    request.block.full_text = "\n".join(f.text for f in request.block.fragments)
    start = text.index(quote)
    request.offer.offer_price = Decimal(amount)
    request.offer.field_evidence["offer_price"] = [
        EvidenceRef(
            fragment_id="f1",
            quote=quote,
            start=start,
            end=start + len(quote),
        )
    ]
    assert observed_offer_price(offer=request.offer, block=request.block) is None
    assert not published_price_evidence_valid(offer=request.offer, block=request.block)
    result = DocumentDecisionPolicy().validate_decision(request=request, decision=None)
    assert result.offer.offer_price is None
    assert result.offer.price_status == "pending"


def test_exported_observed_price_does_not_require_confirmed_price_basis():
    request = scenario(basis="unknown", quantity=None)
    request.offer.field_evidence.pop("price_type")
    assert observed_offer_price(offer=request.offer, block=request.block) == Decimal("12000")


@pytest.mark.parametrize("identity", [True, False])
def test_acceptance_minimum_85_and_request_overrides(identity):
    choice = ChoiceDecision(
        question_id="test", choice="selected", confidence=.85,
        probabilities={"selected": .85, "other": .15},
        model="synthetic", prompt_version="test",
    )
    assert DocumentDecisionPolicy()._accepted(choice, identity=identity)
    probability_field = "identity_probability" if identity else "price_probability"
    stricter = DecisionThresholds(**{probability_field: .86})
    assert not DocumentDecisionPolicy(thresholds=stricter)._accepted(choice, identity=identity)
    for thresholds in [DecisionThresholds(confidence=.86), DecisionThresholds(margin=.71)]:
        policy = DocumentDecisionPolicy(thresholds=thresholds)
        assert not policy._accepted(choice, identity=identity)
    choice.confidence = .84
    assert not DocumentDecisionPolicy()._accepted(choice, identity=identity)
