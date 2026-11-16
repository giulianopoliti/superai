import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect

from app.db.models import Base


def test_migration_creates_job_constraints_and_owner_indexes():
    path = Path(__file__).parents[2] / "alembic/versions/20261015_0017_background_analysis_jobs.py"
    spec = importlib.util.spec_from_file_location("jobs_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite+pysqlite://")
    Base.metadata.create_all(
        engine,
        tables=[table for name, table in Base.metadata.tables.items() if name != "background_jobs"],
    )
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        inspector = inspect(connection)
        assert {row["name"] for row in inspector.get_unique_constraints("background_jobs")} == {
            "uq_background_jobs_idempotency",
        }
        assert {row["name"] for row in inspector.get_indexes("background_jobs")} >= {
            "ix_background_jobs_acquire",
            "ix_background_jobs_lease",
            "ix_background_jobs_owner",
        }
        assert {row["name"] for row in inspector.get_columns("background_jobs")} == {
            column.name for column in Base.metadata.tables["background_jobs"].columns
        }
    engine.dispose()
