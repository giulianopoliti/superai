"""One tenant-scoped batch read, freshness checked before selecting a revision."""

from sqlalchemy import or_, select
from sqlalchemy.orm import load_only

from app.db.models import CatalogInterpretationModel, CatalogVocabularyModel
from app.modules.catalog.product_view import COMPATIBLE_PROMPT_HEADERS, AuxiliaryProduct
from app.modules.procurement.catalog_interpretation import digest, snapshot


def current_interpretations(session, business_id, products):
    if not products:
        return {}
    sources = {p.id: digest(snapshot(p)) for p in products if p.business_id == business_id}
    model = CatalogInterpretationModel
    # Batch is tenant scoped; do not issue one query per product or load provider responses.
    rows = session.scalars(
        select(model).where(
            model.business_id == business_id,
            model.product_id.in_(sources),
            or_(*(model.request["prompt"].as_string().like(header + "\n%")
                  for header in COMPATIBLE_PROMPT_HEADERS)),
        ).options(load_only(
            model.id, model.product_id, model.source_hash, model.config_hash,
            model.attributes, model.standard_name, model.created_at,
        )).order_by(model.created_at.desc(), model.id.desc())
    ).all()
    result = {}
    for row in rows:
        if row.product_id in result or sources.get(row.product_id) != row.source_hash:
            continue
        result[row.product_id] = AuxiliaryProduct(
            interpretation_id=row.id, source_hash=row.source_hash, config_hash=row.config_hash,
            attributes=row.attributes, standard_name=row.standard_name,
        )
    if not result:
        return result
    vocabulary = {
        v.id: v for v in session.scalars(select(CatalogVocabularyModel).where(
            CatalogVocabularyModel.business_id == business_id,
            CatalogVocabularyModel.status == "active",
        )).all()
    }
    for view in result.values():
        for attribute, state in view.attributes.items():
            node = vocabulary.get(state.get("vocabulary_id"))
            if (node and node.attribute == attribute and view.value(attribute)
                    and normalize_label(node.label) == normalize_label(view.value(attribute))):
                view.aliases[attribute] = list(node.aliases)
    return result


def normalize_label(value):
    from app.modules.procurement.normalization import normalize_text
    return normalize_text(str(value))
