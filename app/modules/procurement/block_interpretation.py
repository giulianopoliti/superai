"""Local occurrences and auditable B proposals; business policy confirms separately."""

import re
from decimal import Decimal, InvalidOperation
from typing import get_args
from uuid import NAMESPACE_URL, uuid5

from app.modules.procurement.analysis_contracts import (
    BlockClassificationResult,
    BlockInterpretationResult,
    EvidenceRef,
    FragmentCategory,
    MinimumPurchase,
    OfferInterpretation,
    OfferTextBlock,
    ValueCandidate,
)

_NUMBER = re.compile(r"(?<![\d.,])\d+(?:[.,]\d+)*(?!\d|[.,]\d)")
_PREFIX = re.compile(r"(?:US\$|USD|ARS|\$)\s*$", re.IGNORECASE)
_COMMERCIAL_PREFIX = re.compile(r"\b(?:pack|bulto|caja)\s+(?:de\s+|x\s*)?$", re.I)
_CURRENT_PREFIX = re.compile(r"\b(?:ahora|actual|hoy)\s*(?:[:=-]\s*)?$", re.I)
_SUFFIX = re.compile(
    r"[ \t]*(kg|gr|g|ml|cc|lt|lts|l|u|unidades|unidad|botellas|envases|"
    r"paquetes|sobres|bultos|packs|cajas)\b",
    re.IGNORECASE,
)
_SIZE_UNITS = {
    "kg": "kg",
    "gr": "g",
    "g": "g",
    "ml": "ml",
    "cc": "ml",
    "lt": "l",
    "lts": "l",
    "l": "l",
}
_NON_NAME = re.compile(
    r"^(?:desde\b|m[ií]nim[oa]\b|llevando\b|excepto\b|vigencia\b|"
    r"ofertas?\s+v[aá]lid|precios?\b|antes\b|ahora\b|actual\b|"
    r"hasta\s+agotar\b|v[aá]lid[oa]s?\b|sin\s+iva\b|iva\b|pagando\b|"
    r"por\s+(?:unidad|envase|pack|bulto|caja)\b|\d+\s*%|"
    r"impuestos?\b|san\s+justo\b)",
    re.I,
)
_REFERENCE = re.compile(
    r"\bprecio\s*(?:x\s*(?:lt|lts|litro|l|kg|kilo|u|unidad)|"
    r"por\s+(?:lt|lts|litro|l|kg|kilo))\s*[:=-]?\s*$",
    re.I,
)


def _text_spans(text):
    """Literal lines and contiguous name groups, including those after a price."""
    clean = []
    continuation = False
    for match in re.finditer(r"[^\n;|—]+", text):
        left, right = match.span()
        while left < right and text[left].isspace():
            left += 1
        while right > left and text[right - 1].isspace():
            right -= 1
        line = text[left:right]
        excluded = continuation or bool(_NON_NAME.search(line))
        continuation = excluded and line.endswith("-")
        # A price can follow a name in the same line; its suffix is not a name.
        price = re.search(r"(?:US\$|USD|ARS|\$)\s*\d", line, re.I)
        if price:
            right = left + price.start()
            while right > left and text[right - 1].isspace():
                right -= 1
            line = text[left:right]
        if excluded or not line or not any(c.isalpha() for c in line):
            clean.append(None)
            continue
        clean.append((left, right))
        # Pure presentation spans are useful in a name group, not as a name alone.
        if not re.fullmatch(r"x?\s*\d+(?:[.,]\d+)?\s*(?:ml|cc|g|kg|lt|l|u)\.?", line, re.I):
            yield left, right
    group = []
    for span in clean + [None]:
        if span is not None and (not group or text[group[-1][1] : span[0]].isspace()):
            group.append(span)
            continue
        if len(group) > 1:
            yield group[0][0], group[-1][1]
        group = [span] if span is not None else []


def reference_price_ref(block, candidate):
    """Only an explicit reference label on the same line identifies a reference."""
    fragment = next(f for f in _fragments(block) if f.fragment_id == candidate.fragment_id)
    left = fragment.text.rfind("\n", 0, candidate.start) + 1
    prefix = fragment.text[left : candidate.start]
    label = _REFERENCE.search(prefix)
    if not label:
        return None
    start = left + label.start()
    return EvidenceRef(
        fragment_id=candidate.fragment_id,
        quote=fragment.text[start : candidate.end],
        start=start,
        end=candidate.end,
    )


def _parse_number(literal: str) -> Decimal | None:
    if "." in literal and "," in literal:
        if not re.fullmatch(r"\d{1,3}(?:\.\d{3})+,\d+", literal):
            return None
    elif literal.count(",") > 1:
        return None
    elif literal.count(".") > 1 and not re.fullmatch(r"\d{1,3}(?:\.\d{3})+", literal):
        return None
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", literal):
        normalized = literal.replace(".", "").replace(",", ".")
    else:
        normalized = literal.replace(",", ".")
    try:
        value = Decimal(normalized)
        return value if value.is_finite() else None
    except InvalidOperation:
        return None


def _fragments(block: OfferTextBlock):
    return block.fragments + [
        f for f in block.page_conditions if f.fragment_id in block.condition_refs
    ]


def detect_value_candidates(*, block: OfferTextBlock) -> list[ValueCandidate]:
    """Offsets are Python character indices in each original fragment, never PDF coordinates.

    Number occurrences have no semantic role. Overlapping number/size/quantity/money
    candidates intentionally preserve both the numeric span and its explicit unit.
    Text candidates are literal spans; no word or source fragment is removed.
    """
    result = []
    for fragment in _fragments(block):
        text = fragment.text
        spans = []
        for match in _NUMBER.finditer(text):
            start, end = match.span()
            value = _parse_number(match.group())
            spans.append((start, end, "number", value, None))
            prefix = _PREFIX.search(text[:start])
            suffix = _SUFFIX.match(text[end:])
            if prefix:
                currency = prefix.group().strip().upper()
                # '$' remains '$': no currency is guessed from a country default.
                spans.append((prefix.start(), end, "money", value, currency))
            if suffix:
                raw_unit = suffix.group(1).lower()
                kind = "size" if raw_unit in _SIZE_UNITS else "quantity"
                unit = _SIZE_UNITS.get(raw_unit, raw_unit)
                spans.append((start, end + suffix.end(), kind, value, unit))
            commercial = _COMMERCIAL_PREFIX.search(text[:start])
            if commercial:
                spans.append((commercial.start(), end, "quantity", value, None))
        if fragment.fragment_id in {f.fragment_id for f in block.fragments}:
            spans.extend((left, right, "text", None, None) for left, right in _text_spans(text))
        for start, end, kind, value, unit in sorted(set(spans), key=lambda s: (s[0], s[1], s[2])):
            candidate_id = str(
                uuid5(
                    NAMESPACE_URL,
                    f"{block.run_id}:{block.block_id}:{fragment.fragment_id}:{start}:{end}:{kind}",
                )
            )
            result.append(
                ValueCandidate(
                    candidate_id=candidate_id,
                    block_id=block.block_id,
                    fragment_id=fragment.fragment_id,
                    start=start,
                    end=end,
                    literal=text[start:end],
                    kind=kind,
                    value=value,
                    unit=unit,
                )
            )
    return result


def validate_value_candidates(*, block: OfferTextBlock, candidates: list[ValueCandidate]) -> None:
    """Reject foreign, duplicate, altered or invented occurrences before any HTTP call."""
    fragments = {f.fragment_id: f for f in _fragments(block)}
    parsed = {
        (c.fragment_id, c.start, c.end, c.kind): (c.literal, c.value, c.unit)
        for c in detect_value_candidates(block=block)
    }
    ids = set()
    for candidate in candidates:
        fragment = fragments.get(candidate.fragment_id)
        key = (candidate.fragment_id, candidate.start, candidate.end, candidate.kind)
        if (
            not candidate.candidate_id
            or candidate.candidate_id in ids
            or candidate.candidate_id in {"none", "insufficient", "unknown"}
            or candidate.block_id != block.block_id
            or fragment is None
            or candidate.start >= candidate.end
            or candidate.end > len(fragment.text)
            or fragment.text[candidate.start : candidate.end] != candidate.literal
            or parsed.get(key) != (candidate.literal, candidate.value, candidate.unit)
        ):
            raise ValueError("Invalid or foreign value candidate")
        ids.add(candidate.candidate_id)


def value_question_id(index: int, field: str) -> str:
    return f"value:{index}:{field}"


def price_occurrences(block: OfferTextBlock, candidates: list[ValueCandidate]):
    own_ids = {f.fragment_id for f in block.fragments}
    monies = {(c.fragment_id, c.start, c.end) for c in candidates if c.kind == "money"}
    fragments = {f.fragment_id: f for f in block.fragments}
    result = []
    for c in candidates:
        if c.fragment_id not in own_ids or c.kind not in {"money", "number"}:
            continue
        if c.kind == "money":
            result.append(c)
            continue
        if any(
            f == c.fragment_id and start <= c.start and c.end <= end for f, start, end in monies
        ):
            continue
        if any(
            other.kind in {"size", "quantity"}
            and other.fragment_id == c.fragment_id
            and other.start <= c.start
            and c.end <= other.end
            for other in candidates
        ):
            continue
        source = fragments[c.fragment_id].text
        left = source.rfind("\n", 0, c.start) + 1
        right = source.find("\n", c.end)
        line = source[left : right if right >= 0 else len(source)]
        prefix = source[left : c.start]
        if source[c.end :].lstrip().startswith("%") or re.search(
            r"(?:\b(?:desde|m[ií]nim[oa]|contenido|contiene|descuento)\s*(?:de\s+)?|[xX])$",
            prefix,
            re.I,
        ):
            continue
        if line.strip() == c.literal or re.search(
            r"\b(?:precio|antes|ahora|actual|anterior)\b", prefix, re.I
        ):
            result.append(c)
    return result


def count_option_sets(block, candidates):
    """Admissible literal occurrences per count role, never size or money spans."""
    fragments = {f.fragment_id: f for f in _fragments(block)}
    result = {
        role: set() for role in ("package_quantity", "retail_contents_count", "minimum_purchase")
    }
    for c in candidates:
        if (
            c.kind not in {"number", "quantity"}
            or c.value is None
            or c.value <= 0
            or c.value != c.value.to_integral_value()
        ):
            continue
        if any(
            o.kind in {"size", "money"}
            and o.fragment_id == c.fragment_id
            and o.start <= c.start
            and c.end <= o.end
            for o in candidates
        ):
            continue
        contexts = [
            o
            for o in candidates
            if o.kind == "quantity"
            and o.fragment_id == c.fragment_id
            and o.start <= c.start
            and c.end <= o.end
        ]
        context = max(contexts, key=lambda o: o.end - o.start, default=c)
        source = fragments[c.fragment_id].text
        left = source.rfind("\n", 0, context.start) + 1
        before = source[left : context.start]
        if re.search(r"\b(?:desde|m[ií]nim[oa])\s*(?:compra\s*)?(?:de\s*)?$", before, re.I):
            result["minimum_purchase"].add(c.candidate_id)
        elif re.search(r"(?:\b(?:contenido|contiene)\s*(?:de\s*)?|[xX])$", before, re.I):
            result["retail_contents_count"].add(c.candidate_id)
        elif (
            re.search(r"\b(?:pack|bulto|caja)\s*(?:de\s*|x\s*)?$", before, re.I)
            or re.search(r"\b(?:pack|bulto|caja)\s*(?:de\s*|x\s*)?\d", context.literal, re.I)
            or context.unit in {"botellas", "envases", "paquetes", "sobres"}
        ):
            result["package_quantity"].add(c.candidate_id)
    return result


def brand_candidates(block, candidates):
    """Standalone uppercase lines are closed brand options, not confirmed brands."""
    fragments = {f.fragment_id: f for f in block.fragments}
    return {
        c.candidate_id
        for c in candidates
        if c.kind == "text"
        and c.fragment_id in fragments
        and "\n" not in c.literal
        and c.literal == c.literal.upper()
        and any(ch.isalpha() for ch in c.literal)
        and not re.search(r"\d|[;—]", c.literal)
    }


def classification_option_sets(block: OfferTextBlock, candidates: list[ValueCandidate]):
    """Single source of allowed bindings, also checked when assembling fake/provider DTOs."""
    text = {c.candidate_id for c in candidates if c.kind == "text"}
    size = {c.candidate_id for c in candidates if c.kind == "size"}
    counts = count_option_sets(block, candidates)
    brands = brand_candidates(block, candidates)
    result = {
        f"fragment:{i}": set(get_args(FragmentCategory)) for i, _ in enumerate(block.fragments)
    }
    for i, price in enumerate(price_occurrences(block, candidates)):
        fields = {
            "role": {
                "current_price",
                "previous_price",
                "reference_price",
                "not_price",
                "uncertain",
            },
            "raw_name": text | {"none", "insufficient"},
            "unit_size": size | {"none", "insufficient"},
            "brand": brands | {"none", "insufficient"},
            "package_quantity": counts["package_quantity"] | {"none", "insufficient"},
            "retail_contents_count": counts["retail_contents_count"] | {"none", "insufficient"},
            "minimum_purchase": counts["minimum_purchase"] | {"none", "insufficient"},
            "minimum_scope": {"offer", "page", "unknown"},
            "price_type": {"unit", "pack", "unknown"},
            "basis_evidence": {f.fragment_id for f in _fragments(block)} | {"none", "insufficient"},
        }
        if reference_price_ref(block, price):
            fields = {"role": fields["role"]}
        else:
            for field, options in {"unit_size": size, "brand": brands, **counts}.items():
                if not options:
                    fields.pop(field, None)
            if not counts["minimum_purchase"]:
                fields.pop("minimum_scope", None)
        for field, options in fields.items():
            result[value_question_id(i, field)] = options
    return result


class DeterministicBlockAssembler:
    """Implements BlockAssembler for B. Never confirms identity or price."""

    def assemble_interpretation(
        self,
        *,
        business_id: str,
        block: OfferTextBlock,
        classification: BlockClassificationResult | None,
        candidates: list[ValueCandidate],
    ) -> BlockInterpretationResult:
        validate_value_candidates(block=block, candidates=candidates)
        if not business_id:
            raise ValueError("business_id is required")
        if classification is None:
            return BlockInterpretationResult(
                block_id=block.block_id, reason_codes=["jev_unavailable"]
            )
        if classification.run_id != block.run_id or classification.block_id != block.block_id:
            raise ValueError("Classification belongs to another run/block")
        decisions = {d.question_id: d for d in classification.decisions}
        if len(decisions) != len(classification.decisions):
            raise ValueError("Duplicate question decisions")
        expected_options = classification_option_sets(block, candidates)
        for decision in decisions.values():
            allowed = expected_options.get(decision.question_id)
            if (
                allowed is None
                or set(decision.probabilities) != allowed
                or decision.choice not in allowed
                or decision.probabilities[decision.choice] < max(decision.probabilities.values())
            ):
                raise ValueError("Decision does not match this block's question options")
        candidate_map = {c.candidate_id: c for c in candidates}
        fragment_map = {f.fragment_id: f for f in _fragments(block)}
        for item in classification.classifications:
            expected_id = next(
                (
                    f"fragment:{i}"
                    for i, f in enumerate(block.fragments)
                    if f.fragment_id == item.fragment_id
                ),
                None,
            )
            if (
                item.fragment_id not in {f.fragment_id for f in block.fragments}
                or item.category != item.decision.choice
                or item.decision.question_id != expected_id
                or decisions.get(expected_id) != item.decision
            ):
                raise ValueError("Foreign or inconsistent fragment classification")
        reasons = list(classification.reason_codes)
        offers = []
        previous = []
        references = []

        def ref(candidate):
            return EvidenceRef(
                fragment_id=candidate.fragment_id,
                quote=candidate.literal,
                start=candidate.start,
                end=candidate.end,
            )

        for index, price in enumerate(price_occurrences(block, candidates)):

            def prefix(field, index=index):
                return value_question_id(index, field)

            role = decisions.get(prefix("role"))
            reference = reference_price_ref(block, price)
            if reference is not None:
                references.append(reference)
                if role is not None and role.choice not in {"reference_price", "not_price"}:
                    reasons.append("reference_price_role_overridden")
                continue
            if role is None or role.choice == "uncertain":
                reasons.append("price_occurrence_pending")
                continue
            if role.choice == "previous_price":
                previous.append(ref(price))
                continue
            if role.choice == "reference_price":
                references.append(ref(price))
                reasons.append("reference_price_denominator_pending")
                continue
            if role.choice != "current_price":
                if role.choice != "not_price":
                    reasons.append("invalid_price_role")
                continue
            fragment_text = fragment_map[price.fragment_id].text
            typed_overlap = any(
                c.kind in {"quantity", "size"}
                and c.fragment_id == price.fragment_id
                and c.start <= price.start
                and price.end <= c.end
                for c in candidates
            )
            numeric_context = fragment_text[: price.start]
            bare_price_context = fragment_text.strip() == price.literal or re.search(
                r"\b(?:precio|antes|ahora|actual|anterior)\b", numeric_context, re.I
            )
            if (
                fragment_text[price.end :].lstrip().startswith("%")
                or price.kind == "number"
                and (typed_overlap or not bare_price_context)
            ):
                reasons.append("non_price_occurrence_selected")
                continue
            local_reasons = ["jev_proposal_requires_policy"]
            price_ref = ref(price)
            current_marker = _CURRENT_PREFIX.search(fragment_text[: price.start])
            if current_marker:
                price_ref = EvidenceRef(
                    fragment_id=price.fragment_id,
                    quote=fragment_text[current_marker.start() : price.end],
                    start=current_marker.start(),
                    end=price.end,
                )
            field_evidence = {"offer_price": [price_ref]}

            def select(
                field,
                kinds,
                prefix=prefix,
                local_reasons=local_reasons,
                field_evidence=field_evidence,
            ):
                if prefix(field) not in expected_options:
                    return None
                decision = decisions.get(prefix(field))
                if decision is None or decision.choice == "insufficient":
                    local_reasons.append(f"{field}_pending")
                    return None
                if decision.choice == "none":
                    return None
                selected = candidate_map.get(decision.choice)
                if selected is None or selected.kind not in kinds:
                    local_reasons.append(f"{field}_invalid_reference")
                    return None
                # Partial/unreadable text cannot support a commercially usable value.
                if fragment_map[selected.fragment_id].legibility != "readable":
                    local_reasons.append(f"{field}_unreadable")
                    return None
                field_evidence[field] = [ref(selected)]
                return selected

            name = select("raw_name", {"text"})
            brand = select("brand", {"text"}) if prefix("brand") in expected_options else None
            if brand is not None and (name is None or brand.literal not in name.literal):
                # A brand elsewhere in a multi-product block is not a proven association.
                brand = None
                field_evidence.pop("brand", None)
                local_reasons.append("brand_association_pending")
            size = select("unit_size", {"size"})
            quantity = select("package_quantity", {"number", "quantity"})
            contents = select("retail_contents_count", {"number", "quantity"})
            minimum = select("minimum_purchase", {"number", "quantity"})
            basis = decisions.get(prefix("price_type"))
            price_type = (
                basis.choice if basis and basis.choice in {"unit", "pack", "unknown"} else "unknown"
            )
            basis_selection = decisions.get(prefix("basis_evidence"))
            basis_fragment = fragment_map.get(basis_selection.choice) if basis_selection else None
            if basis_selection is None:
                # Compatibility with callers intentionally requesting only a subset.
                basis_fragment = fragment_map[price.fragment_id]
            basis_refs = []
            if basis_fragment is not None and basis_fragment.legibility == "readable":
                basis_refs.append(
                    EvidenceRef(
                        fragment_id=basis_fragment.fragment_id,
                        quote=basis_fragment.text,
                        start=0,
                        end=len(basis_fragment.text),
                    )
                )
                if basis_fragment.fragment_id != price.fragment_id:
                    basis_refs.append(
                        EvidenceRef(
                            fragment_id=price.fragment_id,
                            quote=fragment_text,
                            start=0,
                            end=len(fragment_text),
                        )
                    )
            basis_text = "\n".join(r.quote for r in basis_refs)
            # A textual interpretation remains a proposal, but even its basis must
            # have explicit support. Bare x48/minimum counts are never such support.
            unit_evidence = bool(
                re.search(
                    r"\b(?:por\s+(?:unidad|envase)|precio\s+unitario)\b",
                    basis_text,
                    re.I,
                )
            )
            pack_evidence = bool(
                re.search(
                    r"\b(?:por\s+(?:bulto|pack|caja)|precio\s+del\s+pack)\b",
                    basis_text,
                    re.I,
                )
            )
            if (
                price_type == "unit"
                and (not unit_evidence or pack_evidence)
                or price_type == "pack"
                and (not pack_evidence or unit_evidence)
            ):
                price_type = "unknown"
                local_reasons.append("price_basis_evidence_pending")
            if price_type == "unknown":
                local_reasons.append("price_basis_pending")
            # Candidate evidence survives abstention; policy decides whether it supports a basis.
            if basis_refs:
                field_evidence["price_type"] = basis_refs

            def count(candidate, field, local_reasons=local_reasons):
                if candidate is None:
                    return None
                if (
                    candidate.value is None
                    or candidate.value <= 0
                    or candidate.value != candidate.value.to_integral_value()
                ):
                    local_reasons.append(f"{field}_invalid_quantity")
                    return None
                return int(candidate.value)

            commercial_count = count(quantity, "package_quantity")
            retail_count = count(contents, "retail_contents_count")
            if quantity:
                contexts = [
                    c
                    for c in candidates
                    if c.kind == "quantity"
                    and c.fragment_id == quantity.fragment_id
                    and c.value == quantity.value
                    and c.start <= quantity.start
                    and quantity.end <= c.end
                ]
                context = max(contexts, key=lambda c: c.end - c.start, default=quantity)
                source = fragment_map[context.fragment_id].text
                excluded_prefix = re.search(
                    r"\b(?:mínimo|minimo|mínima|minima|contenido|contiene)\s*(?:de\s+)?$",
                    source[: context.start],
                    re.I,
                )
                if excluded_prefix:
                    commercial_count = None
                    local_reasons.append("commercial_quantity_is_contents_or_minimum")
                else:
                    field_evidence["package_quantity"] = [ref(context)]
            # Reusing one occurrence for incompatible roles is a contradiction.
            selected_quantities = [c.candidate_id for c in (quantity, contents, minimum) if c]
            # Also detect overlapping numeric/full-unit spans of the same occurrence.
            selected_spans = [c for c in (quantity, contents, minimum) if c]
            overlap = any(
                a.fragment_id == b.fragment_id and a.start < b.end and b.start < a.end
                for i, a in enumerate(selected_spans)
                for b in selected_spans[i + 1 :]
            )
            if len(selected_quantities) != len(set(selected_quantities)) or overlap:
                commercial_count = retail_count = None
                local_reasons.append("quantity_roles_conflict")
            minimum_purchase = None
            if minimum:
                scope_decision = decisions.get(prefix("minimum_scope"))
                scope = scope_decision.choice if scope_decision else "unknown"
                if scope not in {"offer", "page", "unknown"}:
                    scope = "unknown"
                if (
                    minimum.fragment_id not in {f.fragment_id for f in block.fragments}
                    and scope != "page"
                ):
                    scope = "unknown"
                minimum_purchase = MinimumPurchase(
                    value=minimum.value,
                    unit=minimum.unit,
                    scope=scope,
                    refs=[ref(minimum)],
                )
                if minimum.unit is None or scope == "unknown" or overlap:
                    local_reasons.append("minimum_purchase_scope_pending")
                if minimum.value is None or minimum.value <= 0:
                    local_reasons.append("minimum_purchase_invalid_quantity")
            readable = fragment_map[price.fragment_id].legibility == "readable"
            published_price = (
                price.value if readable and price.value is not None and price.value > 0 else None
            )
            if published_price is None:
                local_reasons.append("published_price_pending")
            if name is None:
                local_reasons.append("raw_name_pending")
            currency = price.unit if price.kind == "money" else None
            if currency is None or currency == "$":
                local_reasons.append("currency_pending")
            if price_type == "pack" and (commercial_count is None or commercial_count < 2):
                local_reasons.append("pack_quantity_pending")
            offers.append(
                OfferInterpretation(
                    offer_id=str(
                        uuid5(NAMESPACE_URL, f"{business_id}:{block.block_id}:{price.candidate_id}")
                    ),
                    block_id=block.block_id,
                    business_id=business_id,
                    raw_name=name.literal if name else None,
                    brand=brand.literal if brand else None,
                    unit_size=size.value if size else None,
                    unit=size.unit if size else None,
                    offer_price=published_price,
                    currency=currency,
                    price_type=price_type,
                    package_quantity=commercial_count,
                    retail_contents_count=retail_count,
                    minimum_purchase=minimum_purchase,
                    field_evidence=field_evidence,
                    reason_codes=list(dict.fromkeys(local_reasons)),
                )
            )
        # Prior prices are evidence, never replacements for missing current prices.
        for offer in offers:
            linked_previous = [
                r
                for r in previous
                if r.fragment_id == offer.field_evidence["offer_price"][0].fragment_id
            ]
            if linked_previous:
                offer.field_evidence["previous_price"] = linked_previous
        for offer in offers:
            linked_reference = [
                r
                for r in references
                if len(offers) == 1
                or r.fragment_id == offer.field_evidence["offer_price"][0].fragment_id
            ]
            if linked_reference:
                offer.field_evidence["reference_price"] = linked_reference
        # Same name with different current prices has no proven variant association.
        names = [offer.raw_name for offer in offers if offer.raw_name is not None]
        if len(names) != len(set(names)):
            reasons.append("multiple_prices_same_name_pending")
            for offer in offers:
                offer.reason_codes.append("multiple_prices_same_name_pending")
        if not offers:
            reasons.append("no_usable_offer_proposal")
        return BlockInterpretationResult(
            block_id=block.block_id,
            offers=offers,
            reason_codes=list(dict.fromkeys(reasons)),
            needs_visual_review=False,
        )
