"""Transactional, tenant-scoped taxonomy edits with reversible audit snapshots."""

from copy import deepcopy
from uuid import uuid4

from sqlalchemy import select

from app.db.models import (
    BusinessModel,
    CatalogInterpretationModel,
    CatalogTaxonomyChangeModel,
    CatalogVocabularyModel,
)
from app.db.repositories.catalog_interpretations import ensure_vocabulary
from app.modules.procurement.catalog_interpretation import interpretation_columns
from app.modules.procurement.normalization import normalize_text

VOCAB_FIELDS = (
    "id",
    "business_id",
    "attribute",
    "label",
    "normalized_label",
    "aliases",
    "parent_id",
    "status",
)
ROW_FIELDS = (
    "id",
    "category",
    "subcategory",
    "product_type",
    "standard_name",
    "status",
    "attributes",
)


def taxonomy_rows(session, business_id):
    return session.scalars(
        select(CatalogVocabularyModel)
        .where(
            CatalogVocabularyModel.business_id == business_id,
            CatalogVocabularyModel.attribute.in_(["category", "subcategory"]),
        )
        .order_by(CatalogVocabularyModel.id)
    ).all()


def capture(session, business_id):
    rows = session.scalars(
        select(CatalogInterpretationModel)
        .where(
            CatalogInterpretationModel.business_id == business_id,
        )
        .order_by(CatalogInterpretationModel.id)
    ).all()
    return {
        "vocabulary": [
            {k: deepcopy(getattr(v, k)) for k in VOCAB_FIELDS}
            for v in taxonomy_rows(session, business_id)
        ],
        "interpretations": [{k: deepcopy(getattr(r, k)) for k in ROW_FIELDS} for r in rows],
    }


def refresh_links(session, business_id, change_id, skip_ids=()):
    vocabulary = {
        v.id: v for v in taxonomy_rows(session, business_id) if v.status in {"active", "proposed"}
    }
    for row in session.scalars(
        select(CatalogInterpretationModel).where(
            CatalogInterpretationModel.business_id == business_id,
        )
    ).all():
        if row.id in skip_ids:
            continue
        attributes = deepcopy(row.attributes)
        if "category" not in attributes:
            continue  # Legacy non-hierarchical interpretations keep their original history.
        nodes = [
            (vocabulary.get(attributes.get(k, {}).get("vocabulary_id")), attributes.get(k, {}))
            for k in ("category", "subcategory")
        ]
        nodes = [(node, state) for node, state in nodes if node is not None]
        if not nodes:
            if any(attributes.get(k, {}).get("vocabulary_id") for k in ("category", "subcategory")):
                for key in ("category", "subcategory", "product_type"):
                    attributes[key] = {
                        "status": "unknown",
                        "value": None,
                        "reason": "taxonomy_link_removed",
                    }
                row.attributes = attributes
                for k, value in interpretation_columns(
                    attributes, row.source["original_name"]
                ).items():
                    setattr(row, k, value)
            continue
        child = next(((n, s) for n, s in nodes if n.attribute == "subcategory"), None)
        root = next(((n, s) for n, s in nodes if n.attribute == "category"), None)
        if child:
            parent = vocabulary.get(child[0].parent_id)
            if parent is None or parent.attribute != "category":
                raise ValueError("Subcategory does not have an active category")
            if root is None or root[0].id != parent.id:
                root = (parent, child[1])

        link_threshold = row.request.get("threshold", 0.9)

        def state(node, previous, threshold=link_threshold):
            # Accepting a vocabulary concept does not accept an uncertain product link.
            eligible = previous.get("status") in {"interpreted", "proposed_new"}
            if previous.get("probability") is not None:
                eligible = eligible and previous["probability"] >= threshold
            return {
                **previous,
                "status": (
                    ("interpreted" if node.status == "active" else "proposed_new")
                    if eligible
                    else (
                        "low_probability"
                        if previous.get("probability") is not None
                        and previous["probability"] < threshold
                        else previous.get("status", "unknown")
                    )
                ),
                "value": node.label if eligible and node.status == "active" else None,
                "proposed_value": node.label,
                "vocabulary_id": node.id,
                "parent_id": node.parent_id,
                "taxonomy_change_id": change_id,
            }

        attributes["category"] = state(*root)
        attributes["subcategory"] = (
            state(*child)
            if child
            else (
                deepcopy(
                    attributes.get(
                        "subcategory",
                        {
                            "status": "not_applicable",
                            "value": None,
                            "reason": "no_subcategory_proposed",
                        },
                    )
                )
                if not attributes.get("subcategory", {}).get("value")
                and not attributes.get("subcategory", {}).get("vocabulary_id")
                else {"status": "needs_review", "value": None, "reason": "taxonomy_link_removed"}
            )
        )
        attributes["product_type"] = dict(
            attributes["subcategory"]
            if attributes["subcategory"].get("value")
            else attributes["category"]
        )
        # Only persist when the structural interpretation actually changed.
        if any(
            row.attributes.get(k, {}).get("value") != attributes[k].get("value")
            or row.attributes.get(k, {}).get("vocabulary_id") != attributes[k].get("vocabulary_id")
            or row.attributes.get(k, {}).get("status") != attributes[k].get("status")
            for k in ("category", "subcategory", "product_type")
        ):
            row.attributes = attributes
            for k, value in interpretation_columns(attributes, row.source["original_name"]).items():
                setattr(row, k, value)


def apply_taxonomy_changes(session, business_id: str, operations: list[dict], evidence: dict):
    if not 1 <= len(operations) <= 10:
        raise ValueError("Expected 1 to 10 taxonomy edits")
    session.scalar(select(BusinessModel).where(BusinessModel.id == business_id).with_for_update())
    with session.begin_nested():
        before = capture(session, business_id)
        change_id = str(uuid4())
        allowed_evidence = [evidence.get("goal", ""), *evidence.get("product_names", [])]
        for operation in operations:
            fragment = normalize_text(operation.get("evidence"))
            if (
                not fragment
                or not operation.get("reason")
                or not any(
                    " " + fragment + " " in " " + normalize_text(text) + " "
                    for text in allowed_evidence
                )
            ):
                raise ValueError("Taxonomy edit has no source evidence or reason")
            nodes = taxonomy_rows(session, business_id)
            by_id = {node.id: node for node in nodes}
            action = operation["action"]
            if action == "create_category":
                label = (operation.get("label") or "").strip()
                normalized = normalize_text(label)
                aliases = operation.get("aliases", [])
                if len(aliases) > 8 or any(not a.strip() or len(a) > 120 for a in aliases):
                    raise ValueError("Invalid category aliases")
                if not normalized or len(label) > 120 or any(c.isdigit() for c in label):
                    raise ValueError("Invalid category label")
                matches = [
                    n
                    for n in nodes
                    if n.attribute == "category"
                    and normalized in {normalize_text(a) for a in [n.label, *n.aliases]}
                ]
                if len(matches) > 1:
                    raise ValueError("Ambiguous category aliases")
                node = (
                    matches[0]
                    if matches
                    else ensure_vocabulary(
                        session, business_id, "category", label, aliases, status="active"
                    )
                )
                if node.status not in {"active", "proposed"}:
                    raise ValueError("Cannot reactivate a retired category")
                node.status = "active"
            else:
                node = by_id.get(operation.get("target_id"))
                if node is None or node.status not in {"active", "proposed"}:
                    raise ValueError("Taxonomy target is outside the business or retired")
                if action == "activate":
                    if node.parent_id and by_id[node.parent_id].status != "active":
                        raise ValueError("Activate the parent first")
                    node.status = "active"
                elif action == "rename":
                    label = (operation.get("label") or "").strip()
                    normalized = normalize_text(label)
                    if not normalized or len(label) > 120 or any(c.isdigit() for c in label):
                        raise ValueError("Invalid replacement label")
                    if any(
                        n.id != node.id
                        and n.attribute == node.attribute
                        and normalized in {normalize_text(a) for a in [n.label, *n.aliases]}
                        for n in nodes
                    ):
                        raise ValueError("Renaming would duplicate an existing concept")
                    node.aliases = list(dict.fromkeys([*node.aliases, node.label]))
                    node.label, node.normalized_label = label, normalized
                elif action == "replace_aliases":
                    aliases = operation.get("aliases", [])
                    if len(aliases) > 8 or any(not a.strip() or len(a) > 120 for a in aliases):
                        raise ValueError("Invalid replacement aliases")
                    node.aliases = list(dict.fromkeys(aliases))
                elif action == "move_to_subcategory":
                    parent = by_id.get(operation.get("parent_id"))
                    if parent is None and operation.get("parent_label"):
                        parent = next(
                            (
                                n
                                for n in nodes
                                if n.attribute == "category"
                                and n.normalized_label == normalize_text(operation["parent_label"])
                            ),
                            None,
                        )
                    if (
                        parent is None
                        or parent.id == node.id
                        or parent.attribute != "category"
                        or parent.status != "active"
                    ):
                        raise ValueError("Invalid parent or cycle")
                    if any(n.parent_id == node.id for n in nodes):
                        raise ValueError(
                            "Moving a category with children exceeds the two-level hierarchy"
                        )
                    if any(
                        n.id != node.id
                        and n.attribute == "subcategory"
                        and n.normalized_label == node.normalized_label
                        for n in nodes
                    ):
                        raise ValueError("Duplicate subcategory")
                    node.attribute, node.parent_id, node.status = "subcategory", parent.id, "active"
                else:
                    raise ValueError("Unsupported taxonomy edit")
            session.flush()
        refresh_links(session, business_id, change_id)
        session.flush()
        after = capture(session, business_id)
        change = CatalogTaxonomyChangeModel(
            id=change_id,
            business_id=business_id,
            status="applied",
            operations=operations,
            evidence=evidence,
            before=before,
            after=after,
        )
        session.add(change)
        session.flush()
        return change


def revert_taxonomy_change(session, business_id: str, change_id: str):
    session.scalar(select(BusinessModel).where(BusinessModel.id == business_id).with_for_update())
    with session.begin_nested():
        change = session.scalar(
            select(CatalogTaxonomyChangeModel)
            .where(
                CatalogTaxonomyChangeModel.id == change_id,
                CatalogTaxonomyChangeModel.business_id == business_id,
            )
            .with_for_update()
        )
        if change is None or change.status != "applied":
            raise ValueError("Change is unavailable for rollback")
        if change.evidence.get("audit_scope") == "affected_vocabulary_node":
            raise ValueError("Compact activation requires explicit taxonomy maintenance to revert")
        if capture(session, business_id)["vocabulary"] != change.after["vocabulary"]:
            raise ValueError("Revert subsequent taxonomy changes first")
        current_rows = {r["id"]: r for r in capture(session, business_id)["interpretations"]}
        if any(current_rows.get(row["id"]) != row for row in change.after["interpretations"]):
            raise ValueError(
                "An interpretation changed after this edit; rollback would overwrite it"
            )
        nodes = {n.id: n for n in taxonomy_rows(session, business_id)}
        previous_ids = {n["id"] for n in change.before["vocabulary"]}
        for saved in change.before["vocabulary"]:
            node = nodes[saved["id"]]
            for field, value in saved.items():
                setattr(node, field, deepcopy(value))
        session.flush()
        rows = {
            r.id: r
            for r in session.scalars(
                select(CatalogInterpretationModel).where(
                    CatalogInterpretationModel.business_id == business_id
                )
            ).all()
        }
        for saved in change.before["interpretations"]:
            for field, value in saved.items():
                setattr(rows[saved["id"]], field, deepcopy(value))
        for identifier in nodes.keys() - previous_ids:
            nodes[identifier].status = "retired"
        session.flush()
        # Interpretations created after the edit also follow restored concepts.
        refresh_links(
            session,
            business_id,
            change.id + ":reverted",
            skip_ids={r["id"] for r in change.before["interpretations"]},
        )
        for identifier in nodes.keys() - previous_ids:
            session.delete(nodes[identifier])
        change.status = "reverted"
        session.flush()
