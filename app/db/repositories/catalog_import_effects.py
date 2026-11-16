"""Transactional POS import effects. No workers or model calls are started here."""

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.db.models import (
    CatalogInterpretationNeedModel,
    ProductModel,
    ProductSalesDailyModel,
    SalesItemLineModel,
    utc_now,
)
from app.db.repositories.catalog_product_view import current_interpretations
from app.modules.catalog.product_view import CATALOG_VERSION
from app.modules.procurement.catalog_interpretation import digest, snapshot


def record_interpretation_needs(session, business_id, products):
    session.flush()
    refreshed = []
    for start in range(0, len(products), 500):
        refreshed.extend(session.scalars(select(ProductModel).where(
            ProductModel.business_id == business_id,
            ProductModel.id.in_([p.id for p in products[start:start + 500]]),
        ).execution_options(populate_existing=True)).all())
    products = refreshed
    current = current_interpretations(session, business_id, products)
    table = CatalogInterpretationNeedModel
    insert = pg_insert if session.bind.dialect.name == "postgresql" else sqlite_insert
    # Small batches avoid SQLite/Postgres bind limits; upsert makes repeated imports safe.
    for start in range(0, len(products), 100):
        values = [dict(
            business_id=business_id, product_id=p.id, source_hash=digest(snapshot(p)),
            version=CATALOG_VERSION,
            status="current" if p.id in current else "pending" if p.active else "inactive",
            updated_at=utc_now(),
        ) for p in products[start:start + 100]]
        statement = insert(table).values(values)
        statement = statement.on_conflict_do_update(
            index_elements=[table.business_id, table.product_id],
            set_={k: getattr(statement.excluded, k)
                  for k in ("source_hash", "version", "status", "updated_at")},
            where=(table.source_hash != statement.excluded.source_hash)
            | (table.version != statement.excluded.version)
            | (table.status != statement.excluded.status),
        )
        session.execute(statement)


def reconcile_sales_products(session, business_id, external_ids):
    """Attach missing FKs to exact tenant + external ID, including daily aggregates.

    Aggregates already have a unique tenant/external-ID/day key: changing that FK
    preserves their sums and ticket counts, without inserting or adding sales.
    Never reassign an existing non-null product_id.
    """
    identifiers = sorted(identifier for identifier in external_ids if identifier)
    for start in range(0, len(identifiers), 500):
        for model in (SalesItemLineModel, ProductSalesDailyModel):
            product_id = select(ProductModel.id).where(
                ProductModel.business_id == business_id,
                ProductModel.business_id == model.business_id,
                ProductModel.external_product_id == model.external_product_id,
            ).scalar_subquery()
            session.execute(update(model).where(
                model.business_id == business_id, model.product_id.is_(None),
                model.external_product_id.in_(identifiers[start:start + 500]),
                product_id.is_not(None),
            ).values(product_id=product_id).execution_options(synchronize_session=False))
