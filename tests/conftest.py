"""Shared pytest fixtures.

Tests run against an in-memory SQLite database so they need no external
services. Production uses PostgreSQL (see sql/schema.sql).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base, Source


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture()
def source_factory():
    def _make(session: Session, name: str = "superset") -> Source:
        source = Source(name=name, base_url="https://app.joinsuperset.com")
        session.add(source)
        session.flush()
        return source

    return _make


@pytest.fixture(autouse=True)
def isolated_llm_budget(tmp_path, monkeypatch):
    """Keep tests from spending or waiting on the real model request budget."""
    monkeypatch.setenv("LLM_USAGE_FILE", str(tmp_path / "llm_usage.json"))
    monkeypatch.setenv("LLM_MIN_INTERVAL_SECONDS", "0")
