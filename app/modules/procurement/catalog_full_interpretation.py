"""Generative candidates with closed-option verification and source-backed fields."""

from app.modules.procurement.catalog_interpretation import Option, build_options, build_questions
from app.modules.procurement.catalog_taxonomy import shortlist
from app.modules.procurement.normalization import normalize_text
from app.providers.catalog_taxonomy_proposal import ProposedField

VERSION = "catalog-full-v3"
FIELDS = ("category", "subcategory", "brand", "line", "variant", "packaging")


def supported(source, field):
    if not field.value or not field.evidence:
        return False
    name = " " + normalize_text(source["original_name"]) + " "
    evidence = " " + normalize_text(field.evidence) + " "
    return len(evidence.strip()) > 0 and evidence in name


def matching_entry(entries, attribute, label, parent_id=None):
    normalized = normalize_text(label)
    matches = [
        e
        for e in entries
        if e["attribute"] == attribute
        and (attribute != "subcategory" or e["parent_id"] == parent_id)
        and normalized in {normalize_text(a) for a in [e["label"], *e["aliases"]]}
    ]
    if len(matches) > 1:
        raise ValueError("Ambiguous auxiliary synonym")
    return matches[0] if matches else None


def proposal_vocabulary(source, entries):
    categories = shortlist(source, [e for e in entries if e["attribute"] == "category"], 12)
    parents = {e["id"] for e in categories}
    children = shortlist(
        source,
        [e for e in entries if e["attribute"] == "subcategory" and e["parent_id"] in parents],
        16,
    )
    other = [
        e
        for attribute in ("brand", "line", "variant", "packaging")
        for e in shortlist(source, [e for e in entries if e["attribute"] == attribute], 6)
    ]
    return categories + children + other


def normalize_known_hierarchy(proposal, entries):
    """A known child label keeps its existing parent rather than creating a root."""
    if not proposal.category.value or proposal.subcategory.value:
        return proposal
    name = normalize_text(proposal.category.value)
    matches = [
        e
        for e in entries
        if e["attribute"] == "subcategory"
        and name in {normalize_text(a) for a in [e["label"], *e["aliases"]]}
    ]
    if len(matches) != 1:
        return proposal
    child = matches[0]
    parent = next(
        (e for e in entries if e["id"] == child["parent_id"] and e["attribute"] == "category"), None
    )
    if parent is None:
        return proposal
    return proposal.model_copy(
        update={
            "category": ProposedField(value=parent["label"], evidence=proposal.category.evidence),
            "subcategory": ProposedField(value=child["label"], evidence=proposal.category.evidence),
        }
    )


def full_options(source, proposal, entries, vocabulary, families):
    # Quantities are candidates extracted by deterministic rules, not free generation.
    original = build_options(source, vocabulary)
    options = {
        key: original[key] for key in ("content", "package_quantity", "minimum_order_quantity")
    }
    parent = (
        matching_entry(entries, "category", proposal.category.value)
        if proposal.category.value
        else None
    )
    for attribute in FIELDS:
        field = getattr(proposal, attribute)
        if not supported(source, field):
            options[attribute] = []
            continue
        if attribute in {"category", "subcategory"} and any(c.isdigit() for c in field.value):
            options[attribute] = []
            continue
        parent_id = parent["id"] if parent else None
        entry = matching_entry(entries, attribute, field.value, parent_id)
        candidate = Option(
            entry["id"] if entry else "proposal_" + attribute,
            entry["label"] if entry else field.value.strip(),
            field.evidence,
            "vocabulary" if entry else "model_proposal",
            entry["id"] if entry else None,
        )
        scoped = [
            e
            for e in entries
            if e["attribute"] == attribute
            and (attribute != "subcategory" or (parent and e["parent_id"] == parent_id))
        ]
        # Role extraction must not compete with arbitrary old name fragments.
        others = shortlist(source, scoped, 8) if attribute in {"category", "subcategory"} else []
        # Do not offer a duplicate alias as another independent answer.
        options[attribute] = [candidate] + [
            Option(e["id"], e["label"], source["original_name"], "vocabulary", e["id"])
            for e in others
            if e["id"] != candidate.value
        ]
    options["original_family"] = [
        Option("family_" + str(i), family, source["original_name"], "source")
        for i, family in enumerate(families)
    ]
    return options


def full_questions(options):
    questions = build_questions({k: v for k, v in options.items() if k != "original_family"})
    for question in questions:
        if question["name"] == "subcategory":
            question["instructions"] = (
                "¿Qué subtipo corresponde bajo proposed_category? "
                "Café en saquitos es un subtipo; sin lactosa es un atributo. "
                "Las opciones nuevas también pueden elegirse; none solo si ninguna encaja."
            )
    questions.append(
        {
            "type": "choice",
            "name": "original_family",
            "instructions": "¿Qué familia corresponde? La familia actual puede estar mal.",
            "choices": [
                {"value": o.value, "description": o.label} for o in options["original_family"]
            ]
            + [
                {"value": "unknown", "description": "Datos insuficientes"},
                {"value": "none", "description": "Ninguna familia del sistema corresponde"},
                {"value": "not_applicable", "description": "La familia no aplica"},
            ],
        }
    )
    return questions


def validate_structure(attributes, proposal):
    category = attributes["category"]
    subcategory = attributes["subcategory"]
    if (
        category.get("value")
        and subcategory.get("value")
        and normalize_text(category["value"]) == normalize_text(subcategory["value"])
    ):
        subcategory.update(status="not_applicable", value=None, reason="redundant_subcategory")
    if subcategory.get("value") and (
        not category.get("value")
        or not proposal.category.value
        or normalize_text(category["value"]) != normalize_text(proposal.category.value)
    ):
        subcategory.update(status="conflict", value=None, reason="subcategory_parent_changed")
    # Independent questions must not repeat brand/type as line or variant.
    for key in ("line", "variant"):
        value = attributes[key].get("value")
        if value and any(
            normalize_text(value) == normalize_text(attributes[k].get("value") or "")
            for k in ("brand", "category", "line")
            if k != key
        ):
            attributes[key].update(status="conflict", value=None)
    attributes["product_type"] = dict(subcategory if subcategory.get("value") else category)


def guard_inferred_subtype(attributes, proposal):
    """A brand name alone is not literal evidence for a new product subtype."""
    state = attributes.get("subcategory", {})
    field = proposal.subcategory
    brand_evidence = proposal.brand.evidence
    if (
        state.get("value")
        and field.evidence
        and brand_evidence
        and normalize_text(field.evidence) == normalize_text(brand_evidence)
        and normalize_text(state["value"]) not in normalize_text(field.evidence)
    ):
        state.update(status="unsupported", value=None, reason="subtype_inferred_only_from_brand")
        attributes["product_type"] = dict(attributes["category"])


def family_notice(source, proposal, verified):
    target = verified.get("value")
    accepted = verified["status"] == "interpreted" and target
    if proposal.family_action is not None:
        if proposal.family_action == "propose_new":
            valid = bool(
                proposal.new_family
                and not proposal.suggested_family
                and proposal.family_reason
                and proposal.family_reason.strip()
                and verified.get("status") == "none"
                and (verified.get("probability") or 0) >= 0.9
            )
            return {
                "status": "creation_suggested" if valid else "needs_review",
                "original_family": source.get("category"),
                "suggested_family": None,
                "new_family": proposal.new_family.model_dump() if valid else None,
                "reason": proposal.family_reason,
                "evidence": source["original_name"],
                "requires_user_review": True,
                "original_family_modified": False,
            }
        accepted = bool(
            accepted
            and target == proposal.suggested_family
            and proposal.family_action in {"keep", "reassign"}
            and bool(proposal.family_reason)
        )
        if proposal.family_action == "keep":
            accepted = accepted and target == source.get("category")
    changed = bool(accepted and target != source.get("category"))
    proposed = proposal.suggested_family
    return {
        "status": "change_suggested" if changed else "compatible" if accepted else "needs_review",
        "original_family": source.get("category"),
        "suggested_family": target if accepted else proposed,
        "probability": verified.get("probability"),
        "reason": proposal.family_reason
        if target == proposed
        else (
            "La selección validada corresponde a otra familia del sistema."
            if changed
            else "Familia compatible."
            if accepted
            else "Falta evidencia para confirmar una familia."
        ),
        "evidence": source["original_name"],
        "requires_user_review": not bool(accepted and not changed),
        "original_family_modified": False,
    }


def resolve_auxiliary_attributes(source, proposal, attributes):
    """Accept source-backed generative taxonomy without inventing Decisions confidence."""
    for attribute in ("category", "subcategory"):
        field = getattr(proposal, attribute)
        state = {
            "value": None,
            "evidence": field.evidence,
            "origin": "structured_proposal",
            "probability": None,
            "confidence": None,
            "proposed_value": field.value,
        }
        if field.value:
            valid = supported(source, field) and not any(c.isdigit() for c in field.value)
            state.update(
                value=field.value.strip() if valid else None,
                status="interpreted" if valid else "unsupported",
                reason="source_backed_proposal" if valid else "invalid_evidence_or_label",
            )
        else:
            reason = getattr(proposal, attribute + "_empty_reason")
            state.update(
                status="unknown"
                if reason == "unidentifiable"
                else "not_applicable"
                if reason == "not_applicable"
                else "needs_review",
                reason=reason or "missing_empty_reason",
            )
        attributes[attribute] = state
    # Composition/surface variants are attributes, not a second taxonomy level.
    if proposal.category.value and proposal.variant.value and proposal.subcategory.value:
        combined = normalize_text(proposal.category.value + " " + proposal.variant.value)
        if normalize_text(proposal.subcategory.value) == combined:
            attributes["subcategory"].update(
                value=None, status="not_applicable", reason="attribute_only_distinction"
            )
    validate_structure(attributes, proposal)
    guard_inferred_subtype(attributes, proposal)
