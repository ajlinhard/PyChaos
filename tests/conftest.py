"""Shared pytest fixtures for PyChaos tests.

Fixtures
--------
db_handler
    An in-memory SQLite DatabaseHandler (no Postgres required for unit tests).
workflow_run_id
    A fixed UUID string used to link tasks to a workflow.
"""

from __future__ import annotations

import uuid
import pytest

from pychaos.db.handler import DatabaseHandler, DatabaseSettings


# ---------------------------------------------------------------------------
# In-memory SQLite handler (unit tests — no Postgres required)
# ---------------------------------------------------------------------------

@pytest.fixture
def sqlite_settings(tmp_path) -> DatabaseSettings:
    """DatabaseSettings that point at a per-test SQLite file."""
    db_path = tmp_path / "test.db"
    settings = DatabaseSettings(
        host="",
        port=0,
        name=str(db_path),
        user="",
        password="",
    )
    # Override the computed URL to SQLite
    object.__setattr__(settings, "_sqlite_path", str(db_path))
    return settings


@pytest.fixture
def db_handler(tmp_path) -> DatabaseHandler:
    """Initialised in-memory DatabaseHandler backed by SQLite."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from pychaos.db.models import Base

    engine = create_engine(f"sqlite:///{tmp_path}/test.db", echo=False)
    Base.metadata.create_all(engine)

    handler = DatabaseHandler.__new__(DatabaseHandler)
    handler.settings = DatabaseSettings(
        host="localhost", port=5432, name="test", user="test", password="test"
    )
    handler._engine = engine
    handler._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    handler.logger = __import__("logging").getLogger("test_db_handler")
    return handler


@pytest.fixture
def workflow_run_id() -> str:
    return str(uuid.uuid4())
