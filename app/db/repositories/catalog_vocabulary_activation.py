"""Small audit for an activation that changes neither labels nor existing product links."""

from copy import deepcopy
from uuid import uuid4

from sqlalchemy import select

from app.db.models import CatalogTaxonomyChangeModel, CatalogVocabularyModel
from app.db.repositories.catalog_taxonomy_changes import VOCAB_FIELDS
from app.db.repositories.family_suggestions import lock_business
from app.modules.procurement.normalization import normalize_text


def activate_vocabulary_node(session, business_id, node_id, source):
    lock_business(session, business_id)
    node = session.scalar(
        select(CatalogVocabularyModel)
        .where(
            CatalogVocabularyModel.business_id == business_id, CatalogVocabularyModel.id == node_id
        )
        .with_for_update()
    )
    if (
        node is None
        or node.attribute not in {"category", "subcategory"}
        or node.status == "retired"
    ):
        raise ValueError("Invalid vocabulary node for activation")
    fragment = normalize_text(source["evidence"])
    if (
        not fragment
        or " " + fragment + " " not in " " + normalize_text(source["original_name"]) + " "
    ):
        raise ValueError("Activation requires original-name evidence")
    if node.status == "active":
        return None
    before = {k: deepcopy(getattr(node, k)) for k in VOCAB_FIELDS}
    node.status = "active"
    after = {k: deepcopy(getattr(node, k)) for k in VOCAB_FIELDS}
    change = CatalogTaxonomyChangeModel(
        id=str(uuid4()),
        business_id=business_id,
        status="applied",
        operations=[
            {
                "action": "activate",
                "target_id": node.id,
                "reason": "Propuesta estructurada validada por backend",
                "evidence": source["evidence"],
            }
        ],
        evidence={
            "audit_scope": "affected_vocabulary_node",
            "product_id": source["product_id"],
            "product_names": [source["original_name"]],
        },
        before={"vocabulary": [before], "interpretations": []},
        after={"vocabulary": [after], "interpretations": []},
    )
    session.add(change)
    session.flush()
    return change
