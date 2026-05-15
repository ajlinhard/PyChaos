"""DatabaseHandler — credential management and session lifecycle."""

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
    AIModelModel,
    ArtifactModel,
    Base,
    PromptModel,
    TaskMessageModel,
    TaskMetadataModel,
    TaskRunModel,
    TaskStatus,
    TaskStatusLogModel,
    WorkflowMetadataModel,
    WorkflowRunModel,
)
from .schema import (
    AIModelSchema,
    ArtifactSchema,
    PromptSchema,
    TaskMessageSchema,
    TaskMetadataSchema,
    TaskRunSchema,
    TaskStatusLogSchema,
    WorkflowMetadataSchema,
    WorkflowRunSchema,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class DatabaseSettings(BaseSettings):
    """Database connection settings resolved from PYCHAOS_DB_* env vars."""

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
        return f"postgresql+psycopg2://{self.user}:{pw}@{self.host}:{self.port}/{self.name}"

    @computed_field  # type: ignore[misc]
    @property
    def async_url(self) -> str:
        pw = self.password.get_secret_value()
        return f"postgresql+asyncpg://{self.user}:{pw}@{self.host}:{self.port}/{self.name}"


# ---------------------------------------------------------------------------
# DatabaseHandler
# ---------------------------------------------------------------------------

class DatabaseHandler:
    """Manages the SQLAlchemy engine, sessions, and logging helpers."""

    def __init__(self, settings: DatabaseSettings | None = None) -> None:
        self.settings: DatabaseSettings = settings or DatabaseSettings()
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self, create_tables: bool = True) -> None:
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
                "Database initialised at %s:%s/%s",
                self.settings.host, self.settings.port, self.settings.name,
            )

    def health_check(self) -> bool:
        try:
            with self.get_session() as session:
                session.execute(text("SELECT 1"))
            return True
        except Exception as exc:  # pragma: no cover
            logger.error("Database health check failed: %s", exc)
            return False

    def dispose(self) -> None:
        if self._engine:
            self._engine.dispose()

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    @contextmanager
    def get_session(self) -> Generator[Session, None, None]:
        if self._session_factory is None:
            raise RuntimeError("DatabaseHandler not initialised. Call initialize() first.")
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
    # AI Model registry
    # ------------------------------------------------------------------

    def log_ai_model(self, schema: AIModelSchema) -> None:
        """Upsert an AI model registry entry."""
        with self.get_session() as session:
            if session.get(AIModelModel, schema.id) is None:
                session.add(AIModelModel(
                    id=schema.id,
                    name=schema.name,
                    provider=schema.provider,
                    model_id=schema.model_id,
                    pydantic_model_name=schema.pydantic_model_name,
                    model_type=schema.model_type,
                    version=schema.version,
                    context_window=schema.context_window,
                    dimensions=schema.dimensions,
                    model_settings=dict(schema.model_settings) if schema.model_settings else None,
                    capabilities=schema.capabilities,
                    created_at=schema.created_at,
                ))
        logger.debug("Logged AI model %s (%s)", schema.id, schema.name)

    def get_ai_model(self, model_id: str) -> AIModelSchema | None:
        with self.get_session() as session:
            m = session.get(AIModelModel, model_id)
            return AIModelSchema.model_validate(m) if m else None

    def get_ai_model_by_name(self, name: str) -> AIModelSchema | None:
        with self.get_session() as session:
            m = session.query(AIModelModel).filter_by(name=name).first()
            return AIModelSchema.model_validate(m) if m else None

    # ------------------------------------------------------------------
    # Prompt registry
    # ------------------------------------------------------------------

    def log_prompt(self, schema: PromptSchema) -> None:
        """Upsert a prompt template."""
        with self.get_session() as session:
            if session.get(PromptModel, schema.id) is None:
                session.add(PromptModel(
                    id=schema.id,
                    name=schema.name,
                    version=schema.version,
                    prompt_type=schema.prompt_type,
                    system_prompt=schema.system_prompt,
                    user_template=schema.user_template,
                    variables=schema.variables,
                    messages=schema.messages,
                    dynamic_ref=schema.dynamic_ref,
                    description=schema.description,
                    tags=schema.tags,
                    model_id=schema.model_id,
                    created_at=schema.created_at,
                ))
        logger.debug("Logged prompt %s (%s v%s)", schema.id, schema.name, schema.version)

    def get_prompt(self, prompt_id: str) -> PromptSchema | None:
        with self.get_session() as session:
            m = session.get(PromptModel, prompt_id)
            return PromptSchema.model_validate(m) if m else None

    def get_prompt_by_name(
        self, name: str, version: str | None = None
    ) -> PromptSchema | None:
        with self.get_session() as session:
            q = session.query(PromptModel).filter_by(name=name)
            if version is not None:
                q = q.filter_by(version=version)
            m = q.order_by(PromptModel.created_at.desc()).first()
            return PromptSchema.model_validate(m) if m else None

    # ------------------------------------------------------------------
    # Workflow run — process log
    # ------------------------------------------------------------------

    def log_workflow_run(self, schema: WorkflowRunSchema) -> None:
        """Insert a new WorkflowRun process-log row."""
        with self.get_session() as session:
            session.add(WorkflowRunModel(
                id=schema.id,
                workflow_name=schema.workflow_name,
                status=schema.status,
                started_at=schema.started_at,
                a2a_context_id=schema.a2a_context_id,
                a2a_session_id=schema.a2a_session_id,
            ))
        logger.debug("Logged workflow run %s (%s)", schema.id, schema.workflow_name)

    def log_workflow_metadata(self, schema: WorkflowMetadataSchema) -> None:
        """Insert the workflow-run metadata record (separate from process log)."""
        with self.get_session() as session:
            session.add(WorkflowMetadataModel(
                id=schema.id,
                workflow_run_id=schema.workflow_run_id,
                description=schema.description,
                version=schema.version,
                tags=schema.tags,
                labels=schema.labels,
                config=schema.config,
                created_at=schema.created_at,
            ))
        logger.debug("Logged workflow metadata for run %s", schema.workflow_run_id)

    def update_workflow_status(
        self,
        workflow_run_id: str,
        status: TaskStatus,
        completed_at: datetime | None = None,
    ) -> None:
        with self.get_session() as session:
            m: WorkflowRunModel | None = session.get(WorkflowRunModel, workflow_run_id)
            if m is None:
                logger.warning("update_workflow_status: run %s not found", workflow_run_id)
                return
            m.status = status
            if completed_at is not None:
                m.completed_at = completed_at

    # ------------------------------------------------------------------
    # Task run — process log
    # ------------------------------------------------------------------

    def log_task_run(self, schema: TaskRunSchema) -> None:
        """Insert a new TaskRun process-log row."""
        with self.get_session() as session:
            session.add(TaskRunModel(
                id=schema.id,
                workflow_run_id=schema.workflow_run_id,
                task_name=schema.task_name,
                task_type=schema.task_type,
                status=schema.status,
                started_at=schema.started_at,
                sequence_number=schema.sequence_number,
                a2a_context_id=schema.a2a_context_id,
                model_id=schema.model_id,
                prompt_id=schema.prompt_id,
            ))
        logger.debug("Logged task run %s (%s)", schema.id, schema.task_name)

    def log_task_metadata(self, schema: TaskMetadataSchema) -> None:
        """Insert the task-run metadata record (separate from process log)."""
        with self.get_session() as session:
            session.add(TaskMetadataModel(
                id=schema.id,
                task_run_id=schema.task_run_id,
                description=schema.description,
                tags=schema.tags,
                labels=schema.labels,
                config=schema.config,
                input_schema=schema.input_schema,
                output_schema=schema.output_schema,
                created_at=schema.created_at,
            ))
        logger.debug("Logged task metadata for run %s", schema.task_run_id)

    def update_task_status(
        self,
        task_run_id: str,
        status: TaskStatus,
        completed_at: datetime | None = None,
        error_message: str | None = None,
    ) -> None:
        with self.get_session() as session:
            m: TaskRunModel | None = session.get(TaskRunModel, task_run_id)
            if m is None:
                logger.warning("update_task_status: run %s not found", task_run_id)
                return
            m.status = status
            if completed_at is not None:
                m.completed_at = completed_at
            if error_message is not None:
                m.error_message = error_message

    # ------------------------------------------------------------------
    # A2A audit — task status log
    # ------------------------------------------------------------------

    def log_task_status(self, schema: TaskStatusLogSchema) -> None:
        """Append an A2A state-transition entry to the task status log."""
        with self.get_session() as session:
            session.add(TaskStatusLogModel(
                id=schema.id,
                task_run_id=schema.task_run_id,
                state=schema.state,
                message=schema.message,
                timestamp=schema.timestamp,
            ))
        logger.debug(
            "Logged task status: task=%s state=%s", schema.task_run_id, schema.state
        )

    # ------------------------------------------------------------------
    # A2A audit — task messages
    # ------------------------------------------------------------------

    def log_task_message(self, schema: TaskMessageSchema) -> None:
        """Append an A2A message to the task's history."""
        with self.get_session() as session:
            session.add(TaskMessageModel(
                id=schema.id,
                task_run_id=schema.task_run_id,
                role=schema.role,
                parts=schema.parts,
                message_id=schema.message_id,
                context_id=schema.context_id,
                sequence_number=schema.sequence_number,
                created_at=schema.created_at,
                extra_metadata=schema.extra_metadata,
            ))
        logger.debug(
            "Logged task message: task=%s role=%s seq=%d",
            schema.task_run_id, schema.role, schema.sequence_number,
        )

    def get_task_messages(self, task_run_id: str) -> list[TaskMessageSchema]:
        with self.get_session() as session:
            models = (
                session.query(TaskMessageModel)
                .filter_by(task_run_id=task_run_id)
                .order_by(TaskMessageModel.sequence_number)
                .all()
            )
            return [TaskMessageSchema.model_validate(m) for m in models]

    # ------------------------------------------------------------------
    # Artifacts
    # ------------------------------------------------------------------

    def log_artifact(self, schema: ArtifactSchema) -> None:
        """Insert an artifact row.

        At least one of ``task_run_id`` / ``workflow_run_id`` must be set.
        """
        if schema.task_run_id is None and schema.workflow_run_id is None:
            raise ValueError(
                "ArtifactSchema must have task_run_id or workflow_run_id set."
            )
        with self.get_session() as session:
            session.add(ArtifactModel(
                id=schema.id,
                task_run_id=schema.task_run_id,
                workflow_run_id=schema.workflow_run_id,
                name=schema.name,
                description=schema.description,
                artifact_type=schema.artifact_type,
                parts=schema.parts,
                index=schema.index,
                append=schema.append,
                last_chunk=schema.last_chunk,
                location=schema.location,
                value=schema.value,
                created_at=schema.created_at,
                model_id=schema.model_id,
                prompt_id=schema.prompt_id,
                extra_metadata=schema.extra_metadata,
            ))
        logger.debug("Logged artifact %s (%s)", schema.id, schema.name)

    def get_artifacts(self, task_run_id: str) -> list[ArtifactSchema]:
        with self.get_session() as session:
            models = (
                session.query(ArtifactModel).filter_by(task_run_id=task_run_id).all()
            )
            return [ArtifactSchema.model_validate(m) for m in models]

    def get_workflow_artifacts(self, workflow_run_id: str) -> list[ArtifactSchema]:
        """Fetch workflow-scoped (pre-task) artifacts."""
        with self.get_session() as session:
            models = (
                session.query(ArtifactModel)
                .filter(
                    ArtifactModel.workflow_run_id == workflow_run_id,
                    ArtifactModel.task_run_id.is_(None),
                )
                .all()
            )
            return [ArtifactSchema.model_validate(m) for m in models]

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_workflow_run(self, workflow_run_id: str) -> WorkflowRunSchema | None:
        with self.get_session() as session:
            m = session.get(WorkflowRunModel, workflow_run_id)
            return WorkflowRunSchema.model_validate(m) if m else None

    def get_task_runs(self, workflow_run_id: str) -> list[TaskRunSchema]:
        with self.get_session() as session:
            models = (
                session.query(TaskRunModel)
                .filter_by(workflow_run_id=workflow_run_id)
                .order_by(TaskRunModel.sequence_number)
                .all()
            )
            return [TaskRunSchema.model_validate(m) for m in models]

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> "DatabaseHandler":
        return cls(settings=DatabaseSettings())

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<DatabaseHandler host={self.settings.host!r}"
            f" db={self.settings.name!r} initialised={self._engine is not None}>"
        )
