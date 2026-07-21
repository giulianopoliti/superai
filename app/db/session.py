from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.settings import settings


def create_db_engine(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True)


def create_session_factory(database_url: str) -> sessionmaker[Session]:
    return sessionmaker(bind=create_db_engine(database_url), expire_on_commit=False)


SessionLocal: sessionmaker[Session] | None = (
    create_session_factory(settings.database_url) if settings.database_url else None
)


def get_session() -> Generator[Session]:
    if SessionLocal is None:
        raise RuntimeError("DATABASE_URL is not configured.")

    with SessionLocal() as session:
        yield session
