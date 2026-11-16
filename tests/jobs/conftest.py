import io
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base, BusinessModel, UserModel
from app.jobs.repository import BackgroundJobRepository
from app.jobs.storage import JobFileStorage
from app.modules.procurement.analysis_pipeline_schemas import AnalysisParameters


@pytest.fixture
def sessions():
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add_all([BusinessModel(id="one", name="Uno"), BusinessModel(id="two", name="Dos")])
        session.flush()
        session.add_all(
            [
                UserModel(id="user-one", business_id="one", display_name="Uno"),
                UserModel(id="user-two", business_id="two", display_name="Dos"),
                UserModel(id="colleague", business_id="one", display_name="Otro"),
            ]
        )
        session.commit()
    yield factory
    engine.dispose()


@pytest.fixture
def storage(tmp_path):
    return JobFileStorage(tmp_path / "private-jobs")


@pytest.fixture
def queued_job(sessions, storage):
    def create(*, repository=None, key=None, business_id="one", user_id="user-one"):
        repository = repository or BackgroundJobRepository(sessions)
        stored = storage.save(io.BytesIO(b"%PDF fixture"))
        job, _ = repository.create(
            business_id=business_id,
            created_by_user_id=user_id,
            job_type="supplier_offer_analysis_v1",
            idempotency_key=key or str(uuid4()),
            request_fingerprint="a" * 64,
            file_key=stored.key,
            file_sha256=stored.sha256,
            source_filename="offers.pdf",
            parameters=AnalysisParameters(
                business_id=business_id,
                supplier_name="Proveedor",
                extraction_provider="local_text",
            ).model_dump(mode="json"),
        )
        return repository, job

    return create
