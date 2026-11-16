"""Scoped queries and business-level serialization for family review."""

from sqlalchemy import select

from app.db.models import (
    BusinessModel,
    CatalogFamilyChangeSuggestionModel,
    CatalogFamilyCreationSuggestionModel,
)

MODELS = {
    "change": CatalogFamilyChangeSuggestionModel,
    "creation": CatalogFamilyCreationSuggestionModel,
}


def lock_business(session, business_id):
    business = session.scalar(
        select(BusinessModel).where(BusinessModel.id == business_id).with_for_update()
    )
    if business is None:
        raise ValueError("Business not found")


def get_suggestion(session, business_id, kind, identifier):
    if kind not in MODELS:
        raise ValueError("Invalid suggestion kind")
    model = MODELS[kind]
    result = session.scalar(
        select(model)
        .where(model.business_id == business_id, model.id == identifier)
        .with_for_update()
    )
    if result is None:
        raise ValueError("Suggestion does not belong to this business")
    return result


def list_suggestions(session, business_id, kind="change", status=None):
    if kind not in MODELS:
        raise ValueError("Invalid suggestion kind")
    model = MODELS[kind]
    statement = select(model).where(model.business_id == business_id)
    if status:
        statement = statement.where(model.status == status)
    return session.scalars(statement.order_by(model.created_at, model.id)).all()
