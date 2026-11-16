import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError


def test_additive_migration_and_composite_foreign_key():
    filename = Path(__file__).parents[2] / "alembic/versions/20261015_0015_web_user_access.py"
    spec = importlib.util.spec_from_file_location("web_access_migration", filename)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite+pysqlite://")

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE businesses (id VARCHAR(64) PRIMARY KEY)"))
        connection.execute(
            text(
                "CREATE TABLE users (id VARCHAR(64) PRIMARY KEY, business_id VARCHAR(64), "
                "UNIQUE(business_id, id))"
            )
        )
        connection.execute(text("INSERT INTO businesses VALUES ('one'), ('two')"))
        connection.execute(text("INSERT INTO users VALUES ('operator', 'one')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        with pytest.raises(IntegrityError), connection.begin_nested():
            connection.execute(
                text(
                    "INSERT INTO web_user_access (auth_subject, business_id, user_id) "
                    "VALUES ('subject', 'two', 'operator')"
                )
            )
        connection.execute(
            text(
                "INSERT INTO web_user_access (auth_subject, business_id, user_id) "
                "VALUES ('subject', 'one', 'operator')"
            )
        )
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert not inspect(connection).has_table("web_user_access")
        assert connection.scalar(text("SELECT COUNT(*) FROM users")) == 1
    engine.dispose()


def test_data_api_lockdown_covers_every_business_model():
    from app.db.models import Base

    filename = (
        Path(__file__).parents[2] / "alembic/versions/20261015_0016_private_backend_tables.py"
    )
    spec = importlib.util.spec_from_file_location("private_backend_tables", filename)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    # Every deployed model must be closed. The migration may additionally close
    # persisted tables whose modules are absent from a narrower release.
    # Additive migrations explicitly apply the same lockdown to their new tables.
    assert set(Base.metadata.tables) | {"alembic_version"} <= (
        set(migration.BACKEND_TABLES) | {
            "background_jobs", "catalog_interpretation_needs", "product_analysis_preferences"
        }
    )
    with pytest.raises(RuntimeError, match="must not restore public access"):
        migration.downgrade()
