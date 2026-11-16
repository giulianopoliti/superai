"""Pure observed-field assembly and validation, shared by page and historical readers."""

import json
import re
from uuid import NAMESPACE_URL, uuid5

from app.modules.procurement.analysis_contracts import (
    BlockInterpretationResult,
    EvidenceRef,
    MinimumPurchase,
    OfferInterpretation,
)
from app.modules.procurement.normalization import normalize_text


class AnalysisMetadataTooLargeError(ValueError):
    pass


def validate_metadata_size(metadata, *, max_bytes=4_000_000):
    size = len(
        json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    )
    if size > max_bytes:
        raise AnalysisMetadataTooLargeError(
            "La evidencia supera el lÃ­mite de almacenamiento del anÃ¡lisis."
        )
    return size


def usable_evidence(offer, block):
    """Commercial fields must be supported, even when their price basis is pending."""
    from app.modules.procurement.decision_policy import published_price_evidence_valid

    if not offer.raw_name or not offer.raw_name.strip():
        return False
    if not published_price_evidence_valid(offer=offer, block=block):
        return False
    allowed = {f.fragment_id: f for f in block.fragments}
    allowed.update(
        {f.fragment_id: f for f in block.page_conditions if f.fragment_id in block.condition_refs}
    )
    texts = {}
    for field in ("raw_name", "offer_price"):
        refs = offer.field_evidence.get(field, [])
        if not refs:
            return False
        for ref in refs:
            fragment = allowed.get(ref.fragment_id)
            if (
                fragment is None
                or fragment.legibility != "readable"
                or not ref.quote.strip()
                or ref.quote not in fragment.text
                or (ref.start is None) != (ref.end is None)
            ):
                return False
            if ref.start is not None and (
                ref.end <= ref.start or fragment.text[ref.start : ref.end] != ref.quote
            ):
                return False
        texts[field] = "\n".join(ref.quote for ref in refs)
    name_tokens = set(normalize_text(offer.raw_name).split())
    if not name_tokens <= set(normalize_text(texts["raw_name"]).split()):
        return False
    return True


class StructuredProposalAssembler:
    """Validate A citations per field, preserving unaffected observed proposals."""

    @staticmethod
    def _reference(ref, field, block):
        fragments = block.fragments if ref.scope == "block" else block.page_conditions
        if not 0 <= ref.fragment_index < len(fragments):
            return None
        fragment = fragments[ref.fragment_index]
        if (
            fragment.legibility != "readable"
            or ref.scope == "page_condition"
            and fragment.fragment_id not in block.condition_refs
            or not ref.quote.strip()
        ):
            return None
        if ref.quote not in fragment.text:
            if field != "raw_name":
                return None
            # Recover whitespace-only differences in the declared source. All
            # non-whitespace characters remain exact; ambiguous matches fail.
            pattern = r"\s+".join(re.escape(part) for part in ref.quote.split())
            matches = list(re.finditer(pattern, fragment.text))
            if len(matches) != 1:
                return None
            match = matches[0]
            return EvidenceRef(
                fragment_id=fragment.fragment_id,
                quote=match.group(),
                start=match.start(),
                end=match.end(),
            )
        starts = [m.start() for m in re.finditer(re.escape(ref.quote), fragment.text)]
        if field in {"offer_price", "currency"} and re.search(r"\d", ref.quote):
            # Whole occurrences only: '$1.729' is not '$1.729,00'.
            starts = [
                s
                for s in starts
                if (
                    (s == 0 or fragment.text[s - 1] not in "0123456789.,")
                    and (
                        s + len(ref.quote) == len(fragment.text)
                        or fragment.text[s + len(ref.quote)] not in "0123456789.,"
                    )
                )
            ]
            if len(starts) > 1:
                published = []
                for start in starts:
                    prefix = fragment.text[:start]
                    previous = list(re.finditer(r"\$\s*\d[\d.,]*", prefix))
                    context = prefix[previous[-1].end() :] if previous else prefix
                    if not re.search(
                        r"\b(?:XKG|XLT|XU|por\s+(?:kg|kilo|litro)|antes|anterior|tachado)\s*:?\s*$",
                        context,
                        re.I,
                    ):
                        published.append(start)
                if len(published) == 1:
                    starts = published
            if not starts:
                return None
        unique = len(starts) == 1
        return EvidenceRef(
            fragment_id=fragment.fragment_id,
            quote=ref.quote,
            start=starts[0] if unique else None,
            end=starts[0] + len(ref.quote) if unique else None,
        )

    def assemble_interpretation(self, *, business_id, block, classification, candidates):
        if not business_id.strip():
            raise ValueError("business_id is required")
        offers = []
        for order, proposal in enumerate(block.proposals):
            fields, seen, invalid = {}, set(), set()
            for field in proposal.field_evidence:
                if field.field in seen:
                    invalid.add(field.field)
                seen.add(field.field)
                refs = [self._reference(ref, field.field, block) for ref in field.refs]
                if not refs or any(ref is None for ref in refs):
                    invalid.add(field.field)
                fields[field.field] = [ref for ref in refs if ref is not None]
            if hasattr(proposal, "reference_price"):
                for field in (
                    "brand",
                    "currency",
                    "tax_included",
                    "package_quantity",
                    "retail_contents_count",
                    "reference_price",
                    "reference_unit",
                ):
                    if getattr(proposal, field) is not None and not fields.get(field):
                        invalid.add(field)
                if proposal.brand and fields.get("brand"):
                    quoted = " ".join(ref.quote for ref in fields["brand"])
                    if normalize_text(proposal.brand) != normalize_text(quoted):
                        invalid.add("brand")
            minimum, purchase = proposal.minimum_purchase, None
            if minimum is not None:
                refs = [self._reference(ref, "minimum_purchase", block) for ref in minimum.refs]
                if not refs or any(ref is None for ref in refs):
                    invalid.add("minimum_purchase")
                else:
                    purchase = MinimumPurchase(
                        value=minimum.value,
                        unit=minimum.unit,
                        scope=minimum.scope,
                        refs=refs,
                    )
            values = {
                name: getattr(proposal, name, None)
                for name in (
                    "raw_name",
                    "brand",
                    "unit_size",
                    "unit",
                    "offer_price",
                    "currency",
                    "tax_included",
                    "price_type",
                    "package_quantity",
                    "retail_contents_count",
                    "reference_price",
                    "reference_unit",
                )
            }
            # Invalid optional evidence clears that field only. Mandatory name and
            # amount still pass the policy's shared literal/value validators.
            for field in invalid:
                fields.pop(field, None)
                if field in values:
                    values[field] = "unknown" if field == "price_type" else None
            if {"unit", "unit_size"} & invalid:
                values["unit"], values["unit_size"] = None, None
            if "minimum_purchase" in invalid:
                purchase = None
            offers.append(
                OfferInterpretation(
                    offer_id=str(uuid5(NAMESPACE_URL, f"{business_id}:{block.block_id}:{order}")),
                    business_id=business_id,
                    block_id=block.block_id,
                    **values,
                    minimum_purchase=purchase,
                    field_evidence=fields,
                    reason_codes=[f"{field}_invalid_reference" for field in sorted(invalid)],
                )
            )
        return BlockInterpretationResult(block_id=block.block_id, offers=offers)
