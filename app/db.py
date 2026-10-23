"""Database engine, session factory and helpers."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.models import Base

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            future=True,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(),
            autoflush=False,
            expire_on_commit=False,
            future=True,
        )
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Provide a transactional session scope.

    Commits on success, rolls back on error, always closes the session.
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def create_all() -> None:
    """Create tables from the ORM metadata.

    Convenient for development and tests. Production deployments should apply
    ``sql/schema.sql`` (or an Alembic migration) instead.
    """
    Base.metadata.create_all(bind=get_engine())


def migrate() -> list[str]:
    """Add any columns present in the models but missing from the database.

    ``CREATE TABLE IF NOT EXISTS`` will not add new columns to an existing
    table, so a schema evolution would otherwise require dropping data. This
    issues idempotent ``ALTER TABLE ... ADD COLUMN`` statements instead.
    """
    from sqlalchemy import inspect, text

    engine = get_engine()
    inspector = inspect(engine)
    applied: list[str] = []

    for table in Base.metadata.sorted_tables:
        if table.name not in inspector.get_table_names():
            continue
        existing = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing:
                continue
            column_type = column.type.compile(dialect=engine.dialect)
            default = ""
            if column.default is not None and getattr(column.default, "is_scalar", False):
                default = f" DEFAULT {column.default.arg!r}"
            statement = (
                f'ALTER TABLE {table.name} ADD COLUMN "{column.name}" '
                f"{column_type}{default}"
            )
            with engine.begin() as connection:
                connection.execute(text(statement))
            applied.append(f"{table.name}.{column.name} ({column_type})")

    if applied:
        logger.info("Applied %d column migrations: %s", len(applied), ", ".join(applied))
    else:
        logger.info("Database schema already up to date.")
    return applied
