"""DatabaseHandler — credential management and session lifecycle.

Credentials are pulled from environment variables (or a .env file) via
pydantic-settings.  The handler exposes a simple imperative API used by
BaseWorkflow and BaseTask so callers do not need to manage sessions manually.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Generator

from pydantic import Field, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from .models import (
    ArtifactModel,
    Base,
    TaskRunModel,
    TaskStatus,
    WorkflowRunModel,
)
from .schema import ArtifactSchema, TaskRunSchema, WorkflowRunSchema

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Settings  (pydantic-settings reads PYCHAOS_DB_* env vars / .env)
# ---------------------------------------------------------------------------

class DatabaseSettings(BaseSettings):
    """Database connection settings resolved from the environment."""

    model_config = SettingsConfigDict(
        env_prefix="PYCHAOS_DB_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    host: str = "localhost"
    port: int = 5432
    name: str = "pychaos"
    user: str = "postgres"
    password: SecretStr = Field(default=SecretStr(""))
    schema_name: str = Field("public", alias="schema")
    pool_size: int = 5
    pool_pre_ping: bool = True
    echo: bool = False

    @computed_field  # type: ignore[misc]
    @property
    def sync_url(self) -> str:
        pw = self.password.get_secret_value()
        return (
            f"postgresql+psycopg2://{self.user}:{pw}"
            f"@{self.host}:{self.port}/{self.name}"
        )

    @computed_field  # type: ignore[misc]
    @property
    def async_url(self) -> str:
        pw = self.password.get_secret_value()
        return (
            f"postgresql+asyncpg://{self.user}:{pw}"
            f"@{self.host}:{self.port}/{self.name}"
        )


# ---------------------------------------------------------------------------
# DatabaseHandler
# ---------------------------------------------------------------------------

class DatabaseHandler:
    """Manages the SQLAlchemy engine, sessions, and logging helpers.

    Usage::

        settings = DatabaseSettings()
        db = DatabaseHandler(settings)
        db.initialize()          # creates tables if needed
        db.health_check()        # optional sanity ping

    All public ``log_*`` / ``update_*`` methods open and commit their own
    session so callers stay transaction-free.
    """

    def __init__(self, settings: DatabaseSettings | None = None) -> None:
        self.settings: DatabaseSettings = settings or DatabaseSettings()
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self, create_tables: bool = True) -> None:
        """Create the engine and optionally create all DB tables."""
        self._engine = create_engine(
            self.settings.sync_url,
            pool_size=self.settings.pool_size,
            pool_pre_ping=self.settings.pool_pre_ping,
            echo=self.settings.echo,
        )
        self._session_factory = sessionmaker(bind=self._engine, expire_on_commit=False)
        if create_tables:
            Base.metadata.create_all(self._engine)
            logger.info(
                "Database initialised — tables created/verified at "
                "%s:%s/%s",
                self.settings.host,
                self.settings.port,
                self.settings.name,
            )

    def health_check(self) -> bool:
        """Return True if the DB is reachable."""
        try:
            with self.get_session() as session:
                session.execute(text("SELECT 1"))
            return True
        except Exception as exc:  # pragma: no cover
            logger.error("Database health check failed: %s", exc)
            return False

    def dispose(self) -> None:
        """Close all pooled connections."""
        if self._engine:
            self._engine.dispose()

    # ------------------------------------------------------------------
    # Session context manager
    # ------------------------------------------------------------------

    @contextmanager
    def get_session(self) -> Generator[Session, None, None]:
        """Yield a session; auto-commit on success, rollback on error."""
        if self._session_factory is None:
            raise RuntimeError(
                "DatabaseHandler is not initialised. Call initialize() first."
            )
        session: Session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Workflow run logging
    # ------------------------------------------------------------------

    def log_workflow_run(self, schema: WorkflowRunSchema) -> None:
        """Insert a new WorkflowRun row."""
        with self.get_session() as session:
            session.add(
                WorkflowRunModel(
                    id=schema.id,
                    workflow_name=schema.workflow_name,
                    status=schema.status,
                    started_at=schema.started_at,
                    metadata_=schema.metadata,
                )
            )
        logger.debug("Logged workflow run %s (%s)", schema.id, schema.workflow_name)

    def update_workflow_status(
        self,
        workflow_run_id: str,
        status: TaskStatus,
        completed_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Update status (and optionally completed_at) on an existing run."""
        with self.get_session() as session:
            model: WorkflowRunModel | None = session.get(
                WorkflowRunModel, workflow_run_id
            )
            if model is None:
                logger.warning(
                    "update_workflow_status: run %s not found", workflow_run_id
                )
                return
            model.status = status
            if completed_at is not None:
                model.completed_at = completed_at
            if metadata is not None:
                model.metadata_ = metadata

    # ------------------------------------------------------------------
    # Task run logging
    # ------------------------------------------------------------------

    def log_task_run(self, schema: TaskRunSchema) -> None:
        """Insert a new TaskRun row."""
        with self.get_session() as session:
            session.add(
                TaskRunModel(
                    id=schema.id,
                    workflow_run_id=schema.workflow_run_id,
                    task_name=schema.task_name,
                    task_type=schema.task_type,
                    status=schema.status,
                    started_at=schema.started_at,
                    metadata_=schema.metadata,
                )
            )
        logger.debug("Logged task run %s (%s)", schema.id, schema.task_name)

    def update_task_status(
        self,
        task_run_id: str,
        status: TaskStatus,
        completed_at: datetime | None = None,
        error_message: str | None = None,
    ) -> None:
        """Update status, completed_at, and optional error on a task run."""
        with self.get_session() as session:
            model: TaskRunModel | None = session.get(TaskRunModel, task_run_id)
            if model is None:
                logger.warning("update_task_status: run %s not found", task_run_id)
                return
            model.status = status
            if completed_at is not None:
                model.completed_at = completed_at
            if error_message is not None:
                model.error_message = error_message

    # ------------------------------------------------------------------
    # Artifact logging
    # ------------------------------------------------------------------

    def log_artifact(self, schema: ArtifactSchema) -> None:
        """Insert a new Artifact row."""
        with self.get_session() as session:
            session.add(
                ArtifactModel(
                    id=schema.id,
                    task_run_id=schema.task_run_id,
                    name=schema.name,
                    artifact_type=schema.artifact_type,
                    location=schema.location,
                    value=schema.value,
                    created_at=schema.created_at,
                    metadata_=schema.metadata,
                )
            )
        logger.debug("Logged artifact %s (%s)", schema.id, schema.name)

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_workflow_run(self, workflow_run_id: str) -> WorkflowRunSchema | None:
        """Fetch a WorkflowRun by id and return as Pydantic schema."""
        with self.get_session() as session:
            model = session.get(WorkflowRunModel, workflow_run_id)
            if model is None:
                return None
            return WorkflowRunSchema.model_validate(model)

    def get_task_runs(self, workflow_run_id: str) -> list[TaskRunSchema]:
        """Fetch all task runs for a workflow, returned as Pydantic schemas."""
        with self.get_session() as session:
            models = (
                session.query(TaskRunModel)
                .filter_by(workflow_run_id=workflow_run_id)
                .all()
            )
            return [TaskRunSchema.model_validate(m) for m in models]

    def get_artifacts(self, task_run_id: str) -> list[ArtifactSchema]:
        """Fetch all artifacts for a task run."""
        with self.get_session() as session:
            models = (
                session.query(ArtifactModel)
                .filter_by(task_run_id=task_run_id)
                .all()
            )
            return [ArtifactSchema.model_validate(m) for m in models]

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> "DatabaseHandler":
        """Create a handler using only environment variables / .env file."""
        return cls(settings=DatabaseSettings())

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<DatabaseHandler host={self.settings.host!r}"
            f" db={self.settings.name!r}"
            f" initialised={self._engine is not None}>"
        )
