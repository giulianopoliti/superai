import importlib.util
from io import StringIO
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db.models import Base, BusinessModel, ProductAnalysisPreferenceModel
from app.db.repositories.product_analysis_preferences import SqlProductAnalysisPreferenceRepository
from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.db.repositories.sql_sales import SqlSalesRepository
from app.modules.catalog.analysis_preferences import ProductPreferenceNotFound
from app.modules.procurement.schemas import Product
from app.settings import settings


@pytest.fixture
def preferences(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add_all([BusinessModel(id="one", name="Uno"), BusinessModel(id="two", name="Dos")])
        session.commit()
    catalog = SqlProcurementRepository(factory)
    first = catalog.add_product(Product(
        business_id="one", external_product_id="bread", name="BOLSA PAN 1/2 KILO",
        normalized_name="bolsa pan 1/2 kilo",
    ))
    second = catalog.add_product(Product(
        business_id="two", external_product_id="bread", name="BOLSA PAN 1/2 KILO",
        normalized_name="bolsa pan 1/2 kilo",
    ))
    monkeypatch.setattr(main, "SessionLocal", factory)
    for field, value in {
        "web_auth_enabled": False, "app_env": "test", "scheduler_enabled": False,
        "llm_enabled": False, "agent_enabled": False, "jobs_enabled": False,
        "openai_api_key": None, "kapso_api_key": None,
    }.items():
        monkeypatch.setattr(settings, field, value)
    yield factory, catalog, first, second
    engine.dispose()


def test_preference_is_scoped_reversible_and_survives_catalog_updates(preferences):
    factory, catalog, first, second = preferences
    repository = SqlProductAnalysisPreferenceRepository(factory)
    assert repository.list_excluded_product_ids(business_id="one") == set()
    for _ in range(2):
        repository.set_preference(
            business_id="one", product_id=first.id, exclude_from_sales_rankings=True,
            updated_by_user_id="operator-one",
        )
    catalog.upsert_product(first.model_copy(update={"name": "PAN 500 G"}))
    assert repository.list_excluded_product_ids(business_id="one") == {first.id}
    assert repository.list_excluded_product_ids(business_id="two") == set()
    assert SqlSalesRepository(factory).list_excluded_sales_ranking_product_ids(
        business_id="one"
    ) == {first.id}
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ProductAnalysisPreferenceModel)) == 1
        preference = session.get(ProductAnalysisPreferenceModel, ("one", first.id))
        assert preference.updated_by_user_id == "operator-one"
    with pytest.raises(ProductPreferenceNotFound):
        repository.set_preference(
            business_id="one", product_id=second.id, exclude_from_sales_rankings=True,
            updated_by_user_id=None,
        )
    repository.set_preference(
        business_id="one", product_id=first.id, exclude_from_sales_rankings=False,
        updated_by_user_id="operator-one",
    )
    assert repository.list_excluded_product_ids(business_id="one") == set()


def test_database_rejects_cross_business_preferences(preferences):
    factory, _, first, _ = preferences
    with factory() as session:
        session.add(ProductAnalysisPreferenceModel(
            business_id="two", product_id=first.id, exclude_from_sales_rankings=True,
        ))
        with pytest.raises(IntegrityError):
            session.commit()


def test_preference_api_requires_explicit_product_and_boolean(preferences):
    _, _, first, second = preferences
    with TestClient(main.create_app()) as client:
        endpoint = f"/procurement/products/{first.id}/analysis-preference"
        body = {"business_id": "one", "exclude_from_sales_rankings": True}
        assert client.post(
            endpoint, json={**body, "exclude_from_sales_rankings": "false"},
        ).status_code == 422
        response = client.post(endpoint, json=body)
        assert response.status_code == 200
        assert response.json()["exclude_from_sales_rankings"] is True
        result = client.get("/procurement/products/analysis-preferences?business_id=one")
        assert result.json() == {"business_id": "one", "excluded_product_ids": [first.id]}
        assert result.headers["cache-control"] == "no-store"
        assert client.post(
            f"/procurement/products/{second.id}/analysis-preference", json=body,
        ).status_code == 404
        assert client.post(
            endpoint, json={**body, "updated_by_user_id": "forged"},
        ).status_code == 422
        assert client.post(
            endpoint, json={**body, "exclude_from_sales_rankings": False},
        ).status_code == 200
        assert client.get(
            "/procurement/products/analysis-preferences?business_id=one"
        ).json()["excluded_product_ids"] == []


def test_migration_closes_browser_access_on_postgresql():
    filename = Path(__file__).parents[2] / (
        "alembic/versions/20261030_0019_product_analysis_preferences.py"
    )
    spec = importlib.util.spec_from_file_location("analysis_preferences_migration", filename)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    output = StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={
        "as_sql": True, "output_buffer": output,
    })
    with Operations.context(context):
        migration.upgrade()
    sql = output.getvalue()
    assert "FOREIGN KEY(business_id, product_id) REFERENCES products (business_id, id)" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "REVOKE ALL ON product_analysis_preferences FROM PUBLIC, anon, authenticated" in sql
