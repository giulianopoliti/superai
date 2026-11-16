import importlib.util
from io import StringIO
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError


def migration():
    path = Path(__file__).parents[2] / (
        "alembic/versions/20261021_0018_catalog_interpretation_needs.py"
    )
    spec = importlib.util.spec_from_file_location("catalog_needs", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_catalog_needs_migration_tenant_fk_and_idempotency():
    engine = create_engine("sqlite://")
    event.listen(engine, "connect", lambda c, _: c.execute("PRAGMA foreign_keys=ON"))
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE products (id TEXT PRIMARY KEY, business_id TEXT, UNIQUE(business_id,id))"
        ))
        connection.execute(text("INSERT INTO products VALUES ('p','a')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration().upgrade()
        insert = text(
            "INSERT INTO catalog_interpretation_needs "
            "(business_id,product_id,source_hash,version,status,updated_at) "
            "VALUES (:business,'p','hash','catalog-full-v3','pending','2026-10-21')"
        )
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(insert, {"business": "b"})
        connection.execute(insert, {"business": "a"})
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(insert, {"business": "a"})
        with Operations.context(MigrationContext.configure(connection)):
            migration().downgrade()
        assert not inspect(connection).has_table("catalog_interpretation_needs")


def test_postgres_migration_closes_public_data_api_access():
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        migration().upgrade()
    sql = output.getvalue()
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "REVOKE ALL ON catalog_interpretation_needs FROM PUBLIC, anon, authenticated" in sql
