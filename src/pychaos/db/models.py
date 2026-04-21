"""SQLAlchemy 2.x ORM models for workflow / task / artifact persistence."""

from __future__ import annotations

import uuid
import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ---------------------------------------------------------------------------
# Status enum (single source of truth shared with Pydantic schema)
# ---------------------------------------------------------------------------

class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELED = "canceled"


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# WorkflowRun
# ---------------------------------------------------------------------------

class WorkflowRunModel(Base):
    """One execution of a named workflow."""

    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    workflow_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status: Mapped[TaskStatus] = mapped_column(
        SAEnum(TaskStatus, name="task_status_enum", create_type=True),
        nullable=False,
        default=TaskStatus.PENDING,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Arbitrary caller-supplied metadata (config, tags, run parameters, etc.)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSON, nullable=True
    )

    task_runs: Mapped[list[TaskRunModel]] = relationship(
        back_populates="workflow_run", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<WorkflowRun id={self.id!r} name={self.workflow_name!r} status={self.status}>"


# ---------------------------------------------------------------------------
# TaskRun
# ---------------------------------------------------------------------------

class TaskRunModel(Base):
    """One execution of a task within a workflow run."""

    __tablename__ = "task_runs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    workflow_run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_name: Mapped[str] = mapped_column(String(255), nullable=False)
    task_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[TaskStatus] = mapped_column(
        SAEnum(TaskStatus, name="task_status_enum", create_type=False),
        nullable=False,
        default=TaskStatus.PENDING,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSON, nullable=True
    )

    workflow_run: Mapped[WorkflowRunModel] = relationship(back_populates="task_runs")
    artifacts: Mapped[list[ArtifactModel]] = relationship(
        back_populates="task_run", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<TaskRun id={self.id!r} name={self.task_name!r} type={self.task_type!r}"
            f" status={self.status}>"
        )


# ---------------------------------------------------------------------------
# Artifact
# ---------------------------------------------------------------------------

class ArtifactModel(Base):
    """An output produced by a task run (file, dataset, metric, etc.)."""

    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    task_run_id: Mapped[str] = mapped_column(
        ForeignKey("task_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(128), nullable=False)
    # URI / path to the artifact on disk, S3, etc.
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Inline scalar value or small payload stored as JSON
    value: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSON, nullable=True
    )

    task_run: Mapped[TaskRunModel] = relationship(back_populates="artifacts")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Artifact id={self.id!r} name={self.name!r} type={self.artifact_type!r}>"
        )
