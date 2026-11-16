"""Normalize one attachment's observations without guessing missing conditions."""

from app.modules.procurement.analysis_contracts import EvidenceFragment, OfferTextBlock
from app.modules.procurement.identity import offer_identity_tokens, physical_size
from app.modules.procurement.normalization import normalize_text


def page_blocks(*, extraction, work, run_id, reading_id):
    blocks = []
    reasons = []
    if not extraction.pages:
        return [], ["missing_page_report"]
    seen = set()
    for page_index, page in enumerate(extraction.pages):
        # A one-page attachment unambiguously owns repeated local page 1 entries.
        # Invalid foreign page numbers remain visible but do not create offers.
        if page.page_number != 1:
            reasons.append("invalid_page_number")
            continue
        if page_index:
            reasons.append("repeated_page_entries")
        prefix = f"{run_id}:{reading_id}:{page_index}"
        conditions = [
            EvidenceFragment(
                fragment_id=f"{prefix}:c{i}", order=i, text=f.text, legibility=f.legibility
            )
            for i, f in enumerate(page.page_conditions)
        ]
        if page.coverage.needs_visual_review:
            reasons.append("visual_coverage_incomplete")
        estimate = page.coverage.estimated_offer_blocks
        if estimate is not None and estimate > len(page.blocks):
            reasons.append("low_block_coverage")
        for raw in page.blocks:
            identity = tuple(f.text for f in raw.fragments)
            # Only collapse identical blocks from the same response with identical
            # conditions/proposals. Repeated commercial offers remain distinguishable.
            fingerprint = (identity, raw.model_dump_json(), tuple(f.text for f in conditions))
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            block_id = f"{prefix}:b{len(blocks)}"
            bad_refs = any(i >= len(conditions) for i in raw.page_condition_indices)
            blocks.append(
                OfferTextBlock(
                    block_id=block_id,
                    run_id=run_id,
                    reading_id=reading_id,
                    page_number=work.page,
                    order_on_page=len(blocks),
                    full_text="\n".join(f.text for f in raw.fragments),
                    fragments=[
                        EvidenceFragment(
                            fragment_id=f"{block_id}:f{i}",
                            order=i,
                            text=f.text,
                            legibility=f.legibility,
                        )
                        for i, f in enumerate(raw.fragments)
                    ],
                    proposals=raw.proposals,
                    page_conditions=conditions,
                    condition_refs=[
                        conditions[i].fragment_id
                        for i in raw.page_condition_indices
                        if i < len(conditions)
                    ],
                    warnings=[
                        *raw.warnings,
                        *(["condition_reference_unavailable"] if bad_refs else []),
                    ],
                )
            )
    if len(blocks) <= 1 and work.region is None:
        reasons.append("sparse_page_requires_check")
    return blocks, list(dict.fromkeys(reasons))


def observation_key(offer):
    """Never merge different variant names, sizes, prices or purchase conditions."""
    return (
        normalize_text(offer.brand),
        normalize_text(offer.raw_name),
        offer.unit_size,
        offer.unit,
        offer.offer_price,
        offer.currency,
        offer.tax_included,
        offer.price_type,
        offer.package_quantity,
        offer.retail_contents_count,
        (offer.minimum_purchase.value, offer.minimum_purchase.unit)
        if offer.minimum_purchase
        else None,
    )


def consolidate_observations(observations):
    """Merge corroborating readings, keeping source fragments and explicit conflicts.

    Missing optional fields may be supplied by another reading of the same label.
    Different variants, amounts, sizes or explicit commercial conditions stay apart.
    """
    groups = {}
    result = []

    def minimum(value):
        if not value:
            return None
        unit = normalize_text(value.unit)
        if unit in {"u", "un", "uni", "unit", "unidad", "unidades"}:
            unit = "unit"
        return value.value, unit, value.scope

    for offer, block in observations:
        if not offer.raw_name or offer.offer_price is None:
            result.append((offer, block))
            continue
        tokens = tuple(t for t in offer_identity_tokens(offer) if t not in {"de", "del"})
        key = (
            block.page_number,
            tokens,
            physical_size(offer.unit_size, offer.unit),
            offer.offer_price,
        )
        matches = []
        for prior, owner in groups.get(key, []):
            # Separate products in the same response must not be collapsed.
            if owner.reading_id == block.reading_id:
                continue
            if any(
                getattr(prior, field) is not None
                and getattr(offer, field) is not None
                and getattr(prior, field) != getattr(offer, field)
                for field in (
                    "currency",
                    "tax_included",
                    "package_quantity",
                    "retail_contents_count",
                    "reference_price",
                    "reference_unit",
                )
            ):
                continue
            if (
                prior.price_type != "unknown"
                and offer.price_type != "unknown"
                and prior.price_type != offer.price_type
            ):
                continue
            if (
                minimum(prior.minimum_purchase)
                and minimum(offer.minimum_purchase)
                and minimum(prior.minimum_purchase) != minimum(offer.minimum_purchase)
            ):
                continue
            matches.append((prior, owner))
        if len(matches) != 1:
            groups.setdefault(key, []).append((offer, block))
            result.append((offer, block))
            continue
        prior, owner = matches[0]
        for field in (
            "brand",
            "unit_size",
            "unit",
            "currency",
            "tax_included",
            "price_type",
            "package_quantity",
            "retail_contents_count",
            "minimum_purchase",
            "reference_price",
            "reference_unit",
        ):
            current, observed = getattr(prior, field), getattr(offer, field)
            if (
                current is None or field == "price_type" and current == "unknown"
            ) and observed is not None:
                setattr(prior, field, observed)
                if field in offer.field_evidence:
                    prior.field_evidence[field] = offer.field_evidence[field]
        for field in ("fragments", "page_conditions"):
            existing = {f.fragment_id for f in getattr(owner, field)}
            getattr(owner, field).extend(
                f for f in getattr(block, field) if f.fragment_id not in existing
            )
        owner.condition_refs = list(dict.fromkeys([*owner.condition_refs, *block.condition_refs]))
        for index, fragment in enumerate(owner.fragments):
            fragment.order = index
        owner.full_text = "\n".join(fragment.text for fragment in owner.fragments)
        owner.warnings = list(dict.fromkeys([*owner.warnings, "corroborating_readings_merged"]))
        block.derived_offer_ids.remove(offer.offer_id)
    for index, (offer, block) in enumerate(result):
        if not offer.raw_name or offer.offer_price is None:
            continue
        tokens = set(offer_identity_tokens(offer)) - {"de", "del"}
        for other, other_block in result[index + 1 :]:
            if (
                not other.raw_name
                or block.page_number != other_block.page_number
                or block.reading_id == other_block.reading_id
                or offer.offer_price != other.offer_price
                or physical_size(offer.unit_size, offer.unit)
                != physical_size(other.unit_size, other.unit)
            ):
                continue
            if (
                minimum(offer.minimum_purchase)
                and minimum(other.minimum_purchase)
                and minimum(offer.minimum_purchase) != minimum(other.minimum_purchase)
            ):
                continue
            other_tokens = set(offer_identity_tokens(other)) - {"de", "del"}
            if tokens and other_tokens and (tokens < other_tokens or other_tokens < tokens):
                for observed, source in ((offer, block), (other, other_block)):
                    observed.needs_visual_review = True
                    if "possible_duplicate_observation" not in observed.reason_codes:
                        observed.reason_codes.append("possible_duplicate_observation")
                        source.warnings.append("possible_duplicate_observation")
    return result
