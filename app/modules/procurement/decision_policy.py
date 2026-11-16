"""Deterministic A/B policy. No provider calls, repository access or persistence."""

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.modules.procurement.analysis_contracts import (
    ChoiceDecision,
    ChoiceQuestion,
    EvidenceRef,
    IdentityCandidate,
    OfferDecisionRequest,
    OfferDecisionResult,
    OfferInterpretation,
    OfferTextBlock,
    PolicyResult,
)
from app.modules.procurement.identity import (
    auxiliary_identity_gaps,
    canonical_identity_equal,
    identity_conflicts,
    identity_discriminator_gaps,
    identity_tokens,
    is_multi_product_offer,
    offer_identity_tokens,
    physical_size,
)
from app.modules.procurement.normalization import (
    normalize_text,
    parse_argentine_decimal,
    parse_unit,
)
from app.modules.procurement.price_resolution import promoted_pack_quantity, reference_unit_basis

POLICY_VERSION = "document_decision_policy_v4"


@dataclass(frozen=True)
class DecisionThresholds:
    """Evaluation controls, not calibrated production guarantees."""

    allow_jev_acceptance: bool = True
    identity_probability: float = 0.85
    price_probability: float = 0.85
    confidence: float = 0.85
    margin: float = 0.10

    def __post_init__(self):
        for value in (
            self.identity_probability,
            self.price_probability,
            self.confidence,
            self.margin,
        ):
            if not 0 <= value <= 1:
                raise ValueError("Thresholds must be finite values in [0, 1]")


def build_offer_decision_request(
    *,
    business_id: str,
    block: OfferTextBlock,
    offer: OfferInterpretation,
    candidates: list[IdentityCandidate],
    policy: "DocumentDecisionPolicy | None" = None,
) -> OfferDecisionRequest:
    """Shared DTO with independent pending questions, ready for the JEV provider."""
    request = OfferDecisionRequest(
        business_id=business_id,
        block=block,
        offer=offer,
        candidates=candidates,
        questions=[],
    )
    return (policy or DocumentDecisionPolicy()).prepare_request(request=request)


def evidence_text(block: OfferTextBlock, refs: list[EvidenceRef]) -> str | None:
    """Accept only readable exact citations in this block or explicit page conditions."""
    allowed = {f.fragment_id: f for f in block.fragments}
    allowed.update(
        {f.fragment_id: f for f in block.page_conditions if f.fragment_id in block.condition_refs}
    )
    if not refs:
        return None
    texts = []
    for ref in refs:
        fragment = allowed.get(ref.fragment_id)
        if fragment is None or fragment.legibility != "readable" or not ref.quote.strip():
            return None
        if (ref.start is None) != (ref.end is None):
            return None
        if ref.start is not None:
            if ref.end <= ref.start or fragment.text[ref.start : ref.end] != ref.quote:
                return None
        elif fragment.text.count(ref.quote) != 1:
            return None
        texts.append(ref.quote)
    return "\n".join(texts)


def _price_evidence_valid(offer: OfferInterpretation, block: OfferTextBlock) -> bool:
    text = evidence_text(block, offer.field_evidence.get("offer_price", []))
    if text is None or offer.offer_price is None or offer.offer_price <= 0:
        return False
    # Whole lines with several prices need a narrower selected citation. Do not
    # infer which number is current, nor treat percentage discounts as prices.
    if re.search(r"%|\b(?:antes|anterior|tachado)\b", text, re.I):
        return False
    fragments = {f.fragment_id: f for f in block.fragments + block.page_conditions}
    for ref in offer.field_evidence.get("offer_price", []):
        source = fragments[ref.fragment_id].text
        start = ref.start if ref.start is not None else source.find(ref.quote)
        numeric = re.search(r"(?<!\w)\d+(?:[.,]\d+)*(?!\w)", ref.quote)
        if numeric:
            number_start, number_end = start + numeric.start(), start + numeric.end()
            if (
                number_start
                and source[number_start - 1] in "0123456789.,"
                or number_end < len(source)
                and source[number_end] in "0123456789.,"
            ):
                return False
            # Inspect the original occurrence, not just a narrowed citation.
            # '$90' inside a price is valid; '10' inside 10% or '1' in 1kg is not.
            suffix = source[number_end:]
            if re.match(
                r"\s*(?:%|(?:kg|kgs|grs|gr|g|ml|lt|lts|l|u|un|unidad|unidades|botellas|"
                r"envases|bultos?|packs?|cajas?|paquetes|sobres|toallitas|toallas)\b(?!['’]\w)|"
                r"(?:de\s+)?descuento\b|off\b)",
                suffix,
                re.I,
            ):
                return False
            prefix = source[:number_start]
            line_prefix = normalize_text(prefix.rsplit("\n", 1)[-1])
            if re.search(
                r"\b(?:precio\s+)?(?:x\s*|por\s+)(?:kg|kilo|kilos|lt|lts|litro|litros|l|u|unidad)\b$",
                line_prefix,
            ):
                return False
            if re.search(
                r"\b(?:minimo|minima|contenido|contiene|descuento|ahorro|ahorra)"
                r"(?:\s+(?:de|compra)){0,2}$",
                normalize_text(prefix),
            ) or re.search(r"(?:[-−]\s*\$?\s*|[xX]\s*)$", prefix):
                return False
        if re.search(r"\b(?:antes|anterior|tachado)\b", source, re.I) and not re.search(
            r"\b(?:ahora|actual|hoy)\b", ref.quote, re.I
        ):
            labels = list(
                re.finditer(
                    r"\b(antes|anterior|tachado|ahora|actual|hoy)\b",
                    source[:start],
                    re.I,
                )
            )
            if not labels or labels[-1].group(1).lower() not in {"ahora", "actual", "hoy"}:
                return False
    literals = re.findall(r"(?<!\w)\d+(?:[.,]\d+)*(?!\w)", text)
    if len(literals) != 1:
        return False
    return parse_argentine_decimal(literals[0]) == offer.offer_price


def observed_offer_price(*, offer: OfferInterpretation, block: OfferTextBlock) -> Decimal | None:
    """Observed current amount independent of price basis; sole shared validation helper."""
    if offer.block_id != block.block_id or not _price_evidence_valid(offer, block):
        return None
    return offer.offer_price


def published_price_evidence_valid(*, offer: OfferInterpretation, block: OfferTextBlock) -> bool:
    """Boolean adapter for backend compatibility; does not validate the price basis."""
    return observed_offer_price(offer=offer, block=block) is not None


def _name_evidence_valid(offer: OfferInterpretation, block: OfferTextBlock) -> bool:
    text = evidence_text(block, offer.field_evidence.get("raw_name", []))
    name = normalize_text(offer.raw_name)
    return bool(text and name and name in normalize_text(text))


def _presentation_evidence_valid(offer: OfferInterpretation, block: OfferTextBlock) -> bool:
    refs = offer.field_evidence.get("unit_size", []) or offer.field_evidence.get("raw_name", [])
    text = evidence_text(block, refs)
    return bool(
        text
        and physical_size(*parse_unit(text))
        and physical_size(*parse_unit(text)) == physical_size(offer.unit_size, offer.unit)
    )


def _basis_supported(offer: OfferInterpretation, block: OfferTextBlock, basis: str) -> bool:
    if basis == "unit" and reference_unit_basis(offer, block, evidence_text):
        return True
    if basis == "pack" and promoted_pack_quantity(offer, block):
        return True
    text = evidence_text(block, offer.field_evidence.get("price_type", []))
    if text is None:
        return False
    normalized = normalize_text(text)
    # Inspect the cited occurrence's original context. 'Por pack' inside a
    # purchase condition is not evidence that the displayed amount is a total.
    allowed = {f.fragment_id: f for f in block.fragments + block.page_conditions}
    for ref in offer.field_evidence.get("price_type", []):
        source = allowed[ref.fragment_id].text
        start = ref.start if ref.start is not None else source.find(ref.quote)
        context = normalize_text(source[max(0, start - 32) : start + len(ref.quote)])
        if basis == "pack" and re.search(
            r"\b(?:llevando|comprando|compra minima|venta)\s+(?:por\s+)?(?:pack|bulto|caja)",
            context,
        ):
            return False
    normalized = re.sub(
        r"\b(?:llevando|comprando|compra minima|venta)\s+(?:por\s+)?(?:pack|bulto|caja)\b",
        "",
        normalized,
    )
    unit = bool(
        re.search(
            r"\b(?:por unidad|por un|precio unitario|por envase|por sobre)\b",
            normalized,
        )
    )
    pack = bool(re.search(r"\b(?:por bulto|por pack|por caja|precio del pack)\b", normalized))
    if basis == "unit":
        return unit and not pack
    return pack and not unit if basis == "pack" else False


def _quantity_supported(offer: OfferInterpretation, block: OfferTextBlock) -> bool:
    quantity = offer.package_quantity
    if quantity is not None and quantity == promoted_pack_quantity(offer, block):
        return True
    text = evidence_text(block, offer.field_evidence.get("package_quantity", []))
    if quantity is None or not 2 <= quantity <= 1000 or text is None:
        return False
    refs = offer.field_evidence.get("package_quantity", [])
    excluded = list(offer.field_evidence.get("retail_contents_count", []))
    if offer.minimum_purchase:
        excluded.extend(offer.minimum_purchase.refs)
    if any(ref == other for ref in refs for other in excluded):
        return False
    fragments = {f.fragment_id: f for f in block.fragments + block.page_conditions}
    for ref in refs:
        for other in excluded:
            if ref.fragment_id != other.fragment_id:
                continue
            source = fragments[ref.fragment_id].text
            start = ref.start if ref.start is not None else source.find(ref.quote)
            end = ref.end if ref.end is not None else start + len(ref.quote)
            other_start = other.start if other.start is not None else source.find(other.quote)
            other_end = other.end if other.end is not None else other_start + len(other.quote)
            if start < other_end and other_start < end:
                return False
    normalized = normalize_text(text)
    if re.search(r"\b(?:minimo|minima|contenido|contiene|toallitas|toallas)\b", normalized):
        return False
    patterns = (
        r"\b(?:pack|bulto|caja)\s+(?:de\s+|x\s*)?(\d+)(?![\d.,])\b",
        r"(?<![\d.,])(\d+)(?![\d.,])\s+(?:botellas|envases|paquetes|sobres|unidades)\b",
    )
    quantities = {int(m) for pattern in patterns for m in re.findall(pattern, text, re.I)}
    return quantities == {quantity}


def comparable_unit_price(*, offer: OfferInterpretation, block: OfferTextBlock) -> Decimal | None:
    """Compute only from confirmed, evidence-backed price; never use retail/minimum counts."""
    if (
        offer.block_id != block.block_id
        or offer.price_status != "confirmed"
        or not _price_evidence_valid(offer, block)
        or not _basis_supported(offer, block, offer.price_type)
    ):
        return None
    if offer.price_type == "pack":
        if not _quantity_supported(offer, block):
            return None
        value = offer.offer_price / Decimal(offer.package_quantity)
    elif offer.price_type == "unit":
        value = offer.offer_price
    else:
        return None
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _identity_decision_has_evidence(offer, candidates, block):
    if (not offer.raw_name or offer.unit_size is None
            or not _presentation_evidence_valid(offer, block)):
        return False
    if not offer.brand and not any(
        offer_identity_tokens(offer) == identity_tokens(c.name) for c in candidates
    ):
        return False
    return any(
        physical_size(c.unit_size, c.unit) == physical_size(offer.unit_size, offer.unit)
        for c in candidates
    )


class DocumentDecisionPolicy:
    def __init__(
        self, *, thresholds: DecisionThresholds | None = None, require_identity_evidence=False,
    ):
        self.thresholds = thresholds or DecisionThresholds()
        self.require_identity_evidence = require_identity_evidence

    def prepare_request(self, *, request: OfferDecisionRequest) -> OfferDecisionRequest:
        """Prepare only pending questions; the caller owns whether/how to call JEV."""
        result = self.validate_decision(request=request, decision=None)
        prepared = request.model_copy(deep=True)
        prepared.questions = []
        can_ask_identity = bool(result.offer.identity_status == "pending" and request.candidates
                and (not self.require_identity_evidence or _identity_decision_has_evidence(
                    result.offer, request.candidates, request.block,
                )))
        if self.require_identity_evidence and set(result.reason_codes) & {
            "request_binding_invalid", "human_feedback_conflict", "human_accepted_alternative",
            "multiple_confirmed_identities", "multiple_equivalent_catalog_identities",
        }:
            can_ask_identity = False
        if result.route != "needs_jev" and not (
            self.require_identity_evidence and can_ask_identity
        ):
            return prepared
        if can_ask_identity:
            prepared.questions.append(
                ChoiceQuestion(
                    question_id=f"{request.offer.offer_id}:identity",
                    options=[c.candidate_id for c in request.candidates] + ["none", "insufficient"],
                    prompt="Select identity from evidence and candidates, ignoring prices.",
                )
            )
        if result.offer.price_status == "pending":
            prepared.questions.append(
                ChoiceQuestion(
                    question_id=f"{request.offer.offer_id}:price_basis",
                    options=["unit", "pack", "unknown"],
                    prompt="Interpret price basis using evidence of this offer only.",
                )
            )
        return prepared

    def _accepted(self, choice: ChoiceDecision, *, identity: bool) -> bool:
        probability = choice.probabilities[choice.choice]
        runner = max((p for k, p in choice.probabilities.items() if k != choice.choice), default=0)
        threshold = (
            self.thresholds.identity_probability if identity else self.thresholds.price_probability
        )
        return (
            self.thresholds.allow_jev_acceptance
            and probability >= threshold
            and probability - runner >= self.thresholds.margin
            and choice.confidence >= self.thresholds.confidence
        )

    def validate_decision(
        self,
        *,
        request: OfferDecisionRequest,
        decision: OfferDecisionResult | None,
    ) -> PolicyResult:
        offer = request.offer.model_copy(deep=True)
        block = request.block
        reasons = list(offer.reason_codes)
        offer.identity_status, offer.price_status = "pending", "pending"
        offer.product_id = None
        offer.identity_scope = "single_product"
        offer.group_candidate_product_ids = []
        offer.compatible_product_ids = []
        offer.included_product_ids = []
        offer.group_membership_status = "pending"
        offer.needs_visual_review = False
        if (
            offer.business_id != request.business_id
            or offer.block_id != block.block_id
            or any(c.business_id != request.business_id for c in request.candidates)
            or len({c.candidate_id for c in request.candidates}) != len(request.candidates)
            or any(c.candidate_id in {"none", "insufficient"} for c in request.candidates)
        ):
            reasons.append("request_binding_invalid")
            offer.offer_price = None
            offer.raw_name = None
            return self._result(offer, "needs_human_review", reasons)

        # A proposal is not observed data until its literal references and value
        # agree. The caller's ledger input retains every original proposal.
        name_valid = _name_evidence_valid(offer, block)
        if offer.raw_name is not None and not name_valid:
            offer.raw_name = None
            reasons.append("raw_name_evidence_unverified")
        if offer.offer_price is not None and not _price_evidence_valid(offer, block):
            offer.offer_price = None
            reasons.append("published_price_unverified")
        if offer.unit_size is not None and not _presentation_evidence_valid(offer, block):
            offer.unit_size, offer.unit = None, None
            reasons.append("presentation_evidence_unverified")
        if offer.brand:
            observed_brand = evidence_text(block, offer.field_evidence.get("brand", []))
            observed_name = evidence_text(block, offer.field_evidence.get("raw_name", [])) or ""
            if (normalize_text(offer.brand) != normalize_text(observed_brand)
                    and not set(identity_tokens(offer.brand)) <= set(
                        identity_tokens(observed_name)
                    )):
                offer.brand = None
                reasons.append("brand_evidence_unverified")

        questions = {q.question_id: q for q in request.questions}
        choices: dict[str, ChoiceDecision] = {}
        invalid_decision = False
        memberships = []
        if decision is not None:
            if decision.offer_id != offer.offer_id or len(questions) != len(request.questions):
                invalid_decision = True
            for answer in decision.decisions:
                question = questions.get(answer.question_id)
                purpose = answer.question_id.rsplit(":", 1)[-1]
                if (
                    question is None
                    or set(answer.probabilities) != set(question.options)
                    or answer.choice not in question.options
                    or purpose in choices
                    or purpose not in {"identity", "price_basis"}
                ):
                    invalid_decision = True
                else:
                    choices[purpose] = answer
            seen_members = set()
            for member in decision.memberships:
                if (member.candidate_id in seen_members
                        or member.candidate_id not in {c.candidate_id for c in request.candidates}):
                    invalid_decision = True
                seen_members.add(member.candidate_id)
                for purpose, answer, options in (
                    ("compatibility", member.compatibility,
                     {"compatible", "incompatible", "insufficient"}),
                    ("inclusion", member.inclusion, {"included", "excluded", "insufficient"}),
                ):
                    expected = f"{offer.offer_id}:{member.candidate_id}:{purpose}"
                    question = questions.get(expected)
                    if (answer.question_id != expected or question is None
                            or set(question.options) != options
                            or set(answer.probabilities) != options):
                        invalid_decision = True
                memberships.append(member)
            if invalid_decision:
                choices = {}
                memberships = []
                reasons.append("jev_invalid_response")
            elif not (decision.decisions or decision.memberships) or any(
                c.status != "success" for c in decision.calls
            ):
                reasons.append("jev_provider_error")
                choices = {}
                memberships = []
            returned = {answer.question_id for answer in decision.decisions}
            returned.update(answer.question_id for member in decision.memberships
                            for answer in (member.compatibility, member.inclusion))
            for question_id in questions.keys() - returned:
                reasons.append(f"jev_missing_{question_id.rsplit(':', 1)[-1]}")
            reasons.extend(decision.reason_codes)

        # Identical catalog descriptions or several canonical equivalents do not
        # distinguish internal SKU IDs. A model must not break that tie by ID.
        description_groups = {}
        for candidate in request.candidates:
            key = (
                tuple(identity_tokens(candidate.name)), normalize_text(candidate.brand),
                physical_size(candidate.unit_size, candidate.unit),
            )
            description_groups.setdefault(key, []).append(candidate.candidate_id)
        ambiguous_ids = {
            cid for ids in description_groups.values() if len(ids) > 1 for cid in ids
        }
        equivalents = [
            c.candidate_id for c in request.candidates if canonical_identity_equal(offer, c)
        ]
        if len(equivalents) > 1:
            ambiguous_ids.update(equivalents)
        admissible = []
        decision_admissible = []
        candidate_rejections = []
        group_offer = is_multi_product_offer(offer)
        if group_offer:
            offer.identity_scope = "product_group"
            offer.group_candidate_product_ids = [c.product_id for c in request.candidates]
            reasons.append("multi_product_offer")
        human_alternative = any(
            "human_accepted_alternative" in c.reason_codes for c in request.candidates
        )
        feedback_conflict = any(
            code in reasons
            for code in (
                "feedback_product_unavailable",
                "human_feedback_conflict",
            )
        )
        for candidate in request.candidates:
            codes = candidate.reason_codes
            if candidate.candidate_id in ambiguous_ids and "confirmed_by_feedback" not in codes:
                candidate_rejections.append("multiple_equivalent_catalog_identities")
                continue
            if "confirmed_by_feedback" not in codes:
                gaps = identity_discriminator_gaps(offer, candidate)
                if gaps:
                    candidate_rejections.extend(gaps)
                    continue
            if ("confirmed_by_feedback" not in codes
                    and auxiliary_identity_gaps(offer, candidate)):
                candidate_rejections.extend(auxiliary_identity_gaps(offer, candidate))
                continue
            conflict = bool(offer.raw_name and identity_conflicts(offer, candidate)) or any(
                code in codes
                for code in (
                    "tenant_mismatch",
                    "inactive_product",
                    "brand_conflict",
                    "variant_conflict",
                    "presentation_conflict",
                    "presentation_inconsistent",
                    "feedback_not_identity",
                    "multiple_accepted_feedback",
                    "feedback_product_unavailable",
                )
            )
            feedback_conflict |= (
                "confirmed_by_feedback" in codes
                and conflict
                and "human_accepted_alternative" not in codes
            )
            if not conflict and "human_accepted_alternative" not in codes:
                decision_admissible.append(candidate)
                # Family uncertainty prevents rule-only acceptance, but is not
                # an observed contradiction against this candidate. Decisions
                # receives the entire shortlist and can resolve the wording.
                uncertainty = set(codes) & {
                    "catalog_family_underspecified", "retrieval_truncated",
                    "catalog_variant_ambiguity",
                }
                if uncertainty and "confirmed_by_feedback" not in codes:
                    candidate_rejections.extend(sorted(uncertainty))
                    continue
                admissible.append(candidate)
        deterministic = [
            c
            for c in admissible
            if "confirmed_by_feedback" in c.reason_codes
            or (
                "auto_accepted_identity_v1" in c.reason_codes and canonical_identity_equal(offer, c)
            )
        ]
        if (
            not deterministic
            and name_valid
            and offer.unit_size is not None
            and _price_evidence_valid(offer, block)
            and _basis_supported(offer, block, "pack")
            and _quantity_supported(offer, block)
        ):
            # Price evidence can validate commercial packaging independently of
            # identity. Strip that packaging only after validation, keeping x48
            # retail content and minimum quantities in their own roles.
            identity_offer = offer.model_copy(update={"price_type": "pack"})
            tokens = offer_identity_tokens(identity_offer, commercial_pack_verified=True)
            exact = [c for c in admissible if tokens == identity_tokens(c.name)]
            if len(exact) == 1 and tokens != identity_tokens(offer.raw_name):
                deterministic = exact
        identity_choice = choices.get("identity")
        if group_offer:
            pass  # Candidate IDs describe possible members, never confirmed SKU membership.
        elif feedback_conflict:
            reasons.append("human_feedback_conflict")
        elif human_alternative:
            reasons.append("human_accepted_alternative")
        elif len(deterministic) == 1 and name_valid:
            offer.identity_status, offer.product_id = "confirmed", deterministic[0].product_id
        elif len(deterministic) > 1:
            reasons.append("multiple_confirmed_identities")
        elif identity_choice is not None:
            if identity_choice.choice == "none":
                if self._accepted(identity_choice, identity=True):
                    # A closed shortlist is not an exhaustive catalog search.
                    offer.identity_status = "no_match"
                    reasons.extend(["identity_none", "identity_none_in_candidates"])
                else:
                    reasons.append("identity_none_unaccepted")
            elif identity_choice.choice == "insufficient":
                reasons.append("identity_insufficient")
            else:
                selected = next(
                    (c for c in decision_admissible if c.candidate_id == identity_choice.choice),
                    None,
                )
                if selected is not None and self._accepted(identity_choice, identity=True):
                    if (name_valid and _presentation_evidence_valid(offer, block)
                            and physical_size(offer.unit_size, offer.unit)
                            == physical_size(selected.unit_size, selected.unit)
                            and (offer.brand or set(identity_tokens(offer.raw_name))
                                 == set(identity_tokens(selected.name)))):
                        offer.identity_status, offer.product_id = "confirmed", selected.product_id
                        reasons.append("identity_confirmed_by_decisions")
                    else:
                        reasons.append("identity_evidence_missing")
                else:
                    reasons.append("identity_unaccepted_choice")
        elif not request.candidates:
            reasons.append("no_identity_candidates")
        elif not decision_admissible:
            reasons.append("no_admissible_identity_candidates")
        elif not admissible:
            reasons.append("identity_requires_decision")
        if offer.identity_status == "pending":
            reasons.extend(candidate_rejections)

        if memberships and not feedback_conflict and not human_alternative:
            candidates = {c.candidate_id: c for c in request.candidates}
            for member in memberships:
                candidate = candidates[member.candidate_id]
                brand_tokens = set(identity_tokens(offer.brand or ""))
                candidate_tokens = set(identity_tokens(candidate.name)) | set(
                    identity_tokens(candidate.brand or "")
                )
                size = physical_size(candidate.unit_size, candidate.unit) or physical_size(
                    *parse_unit(candidate.name)
                )
                compatible = (
                    name_valid and offer.unit_size is not None
                    and _presentation_evidence_valid(offer, block)
                    and size == physical_size(offer.unit_size, offer.unit)
                    and bool(brand_tokens) and brand_tokens <= candidate_tokens
                    and not identity_conflicts(offer, candidate)
                    and not any(code in candidate.reason_codes for code in (
                        "inactive_product", "tenant_mismatch", "feedback_not_identity",
                        "multiple_accepted_feedback", "feedback_product_unavailable",
                    ))
                    and member.compatibility.choice == "compatible"
                    and self._accepted(member.compatibility, identity=True)
                )
                if not compatible:
                    continue
                offer.compatible_product_ids.append(candidate.product_id)
                source_name = evidence_text(block, offer.field_evidence.get("raw_name", [])) or ""
                explicit_group = any(
                    phrase in normalize_text(source_name)
                    and phrase in normalize_text(offer.raw_name)
                    for phrase in (
                        "todas las variedades", "todas las lineas", "todas las presentaciones",
                    )
                )
                observed_details = not identity_discriminator_gaps(offer, candidate) and not (
                    auxiliary_identity_gaps(offer, candidate)
                )
                if (member.inclusion.choice == "included"
                        and self._accepted(member.inclusion, identity=True)
                        and member.candidate_id not in ambiguous_ids
                        and (observed_details or explicit_group)):
                    offer.included_product_ids.append(candidate.product_id)
            offer.compatible_product_ids = list(dict.fromkeys(offer.compatible_product_ids))
            offer.included_product_ids = list(dict.fromkeys(offer.included_product_ids))
            if offer.identity_status != "confirmed" and (
                group_offer or len(offer.compatible_product_ids) > 1
            ):
                offer.identity_scope = "product_group"
                offer.group_candidate_product_ids = list(offer.compatible_product_ids)
                if len(offer.compatible_product_ids) > 1:
                    offer.group_membership_status = "compatible"
                    reasons.append("compatible_product_group")
                if len(offer.included_product_ids) > 1:
                    offer.group_membership_status = "confirmed"
                    reasons.append("included_product_group")
                # A group never activates the single-SKU commercial path.
                offer.product_id = None

        basis_choice = choices.get("price_basis")
        promoted_quantity = promoted_pack_quantity(offer, block)
        if promoted_quantity and name_valid:
            offer.package_quantity = promoted_quantity
        basis = offer.price_type
        explicit_bases = [b for b in ("unit", "pack") if _basis_supported(offer, block, b)]
        if len(explicit_bases) > 1:
            basis = "unknown"
        elif len(explicit_bases) == 1:
            basis = explicit_bases[0]
        elif basis_choice is not None:
            if basis_choice.choice not in {"unit", "pack", "unknown"}:
                reasons.append("price_basis_invalid_choice")
                basis = "unknown"
            elif basis_choice.choice == "unknown":
                reasons.append("price_basis_unknown")
                basis = "unknown"
            elif self._accepted(basis_choice, identity=False):
                basis = basis_choice.choice
            elif basis != basis_choice.choice:
                reasons.append("price_basis_unaccepted_choice")
                basis = "unknown"
        if _price_evidence_valid(offer, block) and _basis_supported(offer, block, basis):
            if basis == "unit" or _quantity_supported(offer, block):
                offer.price_status, offer.price_type = "confirmed", basis
            else:
                reasons.append("commercial_quantity_unverified")
        else:
            reasons.append("price_evidence_unverified")  # Historic compatibility.
            if reference_unit_basis(offer, block, evidence_text) is False:
                reasons.append("reference_price_inconsistent")
            reasons.append(
                "published_amount_unverified"
                if not _price_evidence_valid(offer, block)
                else "price_basis_unverified"
            )

        if offer.identity_status != "pending" and offer.price_status == "confirmed":
            return self._result(offer, "resolved", reasons)
        # Only observable visual failures are recoverable. Abstentions, price
        # promotions and provider failures are semantic/human review, not vision.
        visual = any(f.legibility != "readable" for f in block.fragments) or any(
            code in block.reason_codes
            for code in (
                "missing_visual_price",
                "missing_visual_name",
                "visual_evidence_conflict",
            )
        )
        if visual and "visual_attempt_exhausted" not in reasons:
            offer.needs_visual_review = True
            return self._result(
                offer,
                "needs_visual_review",
                reasons + ["recoverable_visual_evidence"],
            )
        if (
            decision is not None
            or feedback_conflict
            or human_alternative
            or len(deterministic) > 1
            or "visual_attempt_exhausted" in reasons
        ):
            return self._result(offer, "needs_human_review", reasons)
        if (
            offer.raw_name
            and decision_admissible
            and offer.identity_status == "pending"
            or _price_evidence_valid(offer, block)
            and not explicit_bases
            and offer.field_evidence.get("price_type")
        ):
            return self._result(offer, "needs_jev", reasons)
        return self._result(offer, "needs_human_review", reasons)

    @staticmethod
    def _result(offer, route, reasons):
        codes = list(dict.fromkeys(reasons))
        offer.reason_codes = codes
        if offer.price_status == "pending":
            # Preserve the proposal in the caller's immutable ledger input. The
            # policy output must not activate legacy pack division when persisted.
            offer.price_type = "unknown"
        return PolicyResult(offer=offer, route=route, reason_codes=codes)
