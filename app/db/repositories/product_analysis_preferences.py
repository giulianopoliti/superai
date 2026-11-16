from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ProductAnalysisPreferenceModel, ProductModel, utc_now
from app.modules.catalog.analysis_preferences import (
    ProductAnalysisPreference,
    ProductPreferenceNotFound,
)


class SqlProductAnalysisPreferenceRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def list_excluded_product_ids(self, *, business_id: str) -> set[str]:
        with self._session_factory() as session:
            return set(session.scalars(select(ProductAnalysisPreferenceModel.product_id).where(
                ProductAnalysisPreferenceModel.business_id == business_id,
                ProductAnalysisPreferenceModel.exclude_from_sales_rankings.is_(True),
            )))

    def set_preference(
        self, *, business_id: str, product_id: str, exclude_from_sales_rankings: bool,
        updated_by_user_id: str | None,
    ) -> ProductAnalysisPreference:
        with self._session_factory() as session:
            exists = session.scalar(select(ProductModel.id).where(
                ProductModel.business_id == business_id, ProductModel.id == product_id,
            ))
            if exists is None:
                raise ProductPreferenceNotFound("No se encontró el producto en este comercio.")
            insert = (
                pg_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
            )
            values = {
                "exclude_from_sales_rankings": exclude_from_sales_rankings,
                "updated_by_user_id": updated_by_user_id,
                "updated_at": utc_now(),
            }
            statement = insert(ProductAnalysisPreferenceModel).values(
                business_id=business_id, product_id=product_id, **values,
            ).on_conflict_do_update(index_elements=["business_id", "product_id"], set_=values)
            session.execute(statement)
            session.commit()
            return ProductAnalysisPreference(
                business_id=business_id, product_id=product_id,
                exclude_from_sales_rankings=exclude_from_sales_rankings,
                updated_at=values["updated_at"],
            )
