"""Tenant-scoped persistence for the auxiliary catalog dataset."""

from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import CatalogInterpretationModel, CatalogVocabularyModel, ProductModel
from app.modules.procurement.normalization import normalize_text


def vocabulary_id(business_id: str, attribute: str, label: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"stock-ai/{business_id}/{attribute}/{normalize_text(label)}"))


def ensure_vocabulary(
    session,
    business_id: str,
    attribute: str,
    label: str,
    aliases: list,
    *,
    status: str,
    parent_id: str | None = None,
    update_aliases: bool = True,
) -> CatalogVocabularyModel:
    if parent_id is not None:
        parent = session.scalar(
            select(CatalogVocabularyModel).where(
                CatalogVocabularyModel.id == parent_id,
                CatalogVocabularyModel.business_id == business_id,
                CatalogVocabularyModel.attribute == "category",
            )
        )
        if attribute != "subcategory" or parent is None:
            raise ValueError("Invalid taxonomy parent for this business")
    elif attribute == "subcategory":
        raise ValueError("Subcategory requires a parent")
    normalized = normalize_text(label)
    statement = select(CatalogVocabularyModel).where(
        CatalogVocabularyModel.business_id == business_id,
        CatalogVocabularyModel.attribute == attribute,
        CatalogVocabularyModel.normalized_label == normalized,
    )
    existing = session.scalar(statement)
    if existing is None:
        matches = [
            entry
            for entry in session.scalars(
                select(CatalogVocabularyModel).where(
                    CatalogVocabularyModel.business_id == business_id,
                    CatalogVocabularyModel.attribute == attribute,
                    CatalogVocabularyModel.parent_id == parent_id,
                    CatalogVocabularyModel.status.in_(["active", "proposed"]),
                )
            ).all()
            if normalized in {normalize_text(a) for a in entry.aliases}
        ]
        if len(matches) > 1:
            raise ValueError("Ambiguous vocabulary alias")
        existing = matches[0] if matches else None
    if existing:
        if existing.parent_id != parent_id:
            raise ValueError("Existing label belongs to another parent; review required")
        # Explicit seed edits are versioned vocabulary maintenance, not AI promotion.
        if update_aliases and status == "active" and existing.status == "active":
            existing.aliases = aliases
        return existing
    row = CatalogVocabularyModel(
        id=vocabulary_id(business_id, attribute, label),
        business_id=business_id,
        attribute=attribute,
        label=label,
        normalized_label=normalized,
        aliases=aliases,
        status=status,
        parent_id=parent_id,
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        existing = session.scalar(statement)
        if existing is None:
            raise
        if existing.parent_id != parent_id:
            raise ValueError("Concurrent proposal belongs to another taxonomy parent") from None
        return existing
    return row


def find_interpretation(
    session, *, business_id: str, product_id: str, source_hash: str, config_hash: str
):
    return session.scalar(
        select(CatalogInterpretationModel).where(
            CatalogInterpretationModel.business_id == business_id,
            CatalogInterpretationModel.product_id == product_id,
            CatalogInterpretationModel.source_hash == source_hash,
            CatalogInterpretationModel.config_hash == config_hash,
        )
    )


def save_interpretation(session, row: CatalogInterpretationModel) -> CatalogInterpretationModel:
    product = session.scalar(
        select(ProductModel)
        .where(
            ProductModel.id == row.product_id,
            ProductModel.business_id == row.business_id,
        )
        .with_for_update()
    )
    if product is None:
        raise ValueError("Product does not belong to the requested business")
    # A concurrent POS import must not attach stale evidence to changed source data.
    from app.modules.procurement.catalog_interpretation import digest, snapshot

    if digest(snapshot(product)) != row.source_hash:
        raise ValueError("Product changed during interpretation; retry with fresh data")
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        existing = find_interpretation(
            session,
            business_id=row.business_id,
            product_id=row.product_id,
            source_hash=row.source_hash,
            config_hash=row.config_hash,
        )
        if existing is None:
            raise
        row = existing
    from app.db.repositories.catalog_import_effects import record_interpretation_needs

    record_interpretation_needs(session, row.business_id, [product])
    return row
