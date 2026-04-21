"""Pydantic v2 schemas for DB models and A2A protocol types.

A2A (Agent-to-Agent) protocol types follow the Google A2A specification:
https://google.github.io/A2A/

These schemas serve dual purposes:
1. Round-trip serialisation with SQLAlchemy ORM models (from_attributes=True)
2. A2A-compliant message passing between workflow agents
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from .models import TaskStatus

__all__ = [
    # A2A protocol types
    "TextPart",
    "DataPart",
    "FilePart",
    "Part",
    "A2AMessage",
    "A2ATaskState",
    "A2ATaskStatus",
    "A2AArtifact",
    "A2ATask",
    # DB schemas
    "ArtifactSchema",
    "TaskRunSchema",
    "WorkflowRunSchema",
    "TaskStatus",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_id() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ===========================================================================
# A2A Protocol types
# ===========================================================================

class TextPart(BaseModel):
    """A plain-text content part."""

    type: Literal["text"] = "text"
    text: str
    metadata: dict[str, Any] | None = None


class DataPart(BaseModel):
    """A structured JSON data content part."""

    type: Literal["data"] = "data"
    data: dict[str, Any]
    metadata: dict[str, Any] | None = None


class FileInfo(BaseModel):
    uri: str
    mime_type: str | None = Field(None, alias="mimeType")
    name: str | None = None
    model_config = ConfigDict(populate_by_name=True)


class FilePart(BaseModel):
    """A file reference content part."""

    type: Literal["file"] = "file"
    file: FileInfo
    metadata: dict[str, Any] | None = None


# Discriminated union — pydantic picks the right model from the "type" field
Part = Annotated[
    Union[TextPart, DataPart, FilePart],
    Field(discriminator="type"),
]


class A2AMessage(BaseModel):
    """An A2A protocol message (user ↔ agent exchange)."""

    role: Literal["user", "agent"]
    parts: list[Part] = Field(default_factory=list)
    message_id: str = Field(default_factory=_new_id, alias="messageId")
    task_id: str | None = Field(None, alias="taskId")
    context_id: str | None = Field(None, alias="contextId")
    metadata: dict[str, Any] | None = None
    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def text(cls, role: Literal["user", "agent"], text: str, **kwargs: Any) -> "A2AMessage":
        """Convenience constructor for a single-text-part message."""
        return cls(role=role, parts=[TextPart(text=text)], **kwargs)

    @classmethod
    def data(cls, role: Literal["user", "agent"], payload: dict[str, Any], **kwargs: Any) -> "A2AMessage":
        """Convenience constructor for a single-data-part message."""
        return cls(role=role, parts=[DataPart(data=payload)], **kwargs)


class A2ATaskState(str, Enum):
    """A2A task lifecycle states."""

    SUBMITTED = "submitted"
    WORKING = "working"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    INPUT_REQUIRED = "input-required"

    @classmethod
    def from_task_status(cls, status: TaskStatus) -> "A2ATaskState":
        """Map internal TaskStatus → A2A state."""
        _map = {
            TaskStatus.PENDING: cls.SUBMITTED,
            TaskStatus.RUNNING: cls.WORKING,
            TaskStatus.COMPLETED: cls.COMPLETED,
            TaskStatus.FAILED: cls.FAILED,
            TaskStatus.CANCELED: cls.CANCELED,
            TaskStatus.SKIPPED: cls.CANCELED,
        }
        return _map[status]


class A2ATaskStatus(BaseModel):
    """Current state of an A2A task."""

    state: A2ATaskState
    message: A2AMessage | None = None
    timestamp: datetime = Field(default_factory=_utcnow)


class A2AArtifact(BaseModel):
    """An output artifact produced by an A2A task."""

    name: str | None = None
    description: str | None = None
    parts: list[Part] = Field(default_factory=list)
    index: int = 0
    append: bool = False
    last_chunk: bool = Field(True, alias="lastChunk")
    metadata: dict[str, Any] | None = None
    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def from_value(cls, name: str, value: dict[str, Any], **kwargs: Any) -> "A2AArtifact":
        """Create a data-typed artifact from a dict value."""
        return cls(name=name, parts=[DataPart(data=value)], **kwargs)

    @classmethod
    def from_file(cls, name: str, uri: str, mime_type: str | None = None, **kwargs: Any) -> "A2AArtifact":
        """Create a file-typed artifact."""
        return cls(
            name=name,
            parts=[FilePart(file=FileInfo(uri=uri, mime_type=mime_type))],
            **kwargs,
        )


class A2ATask(BaseModel):
    """An A2A-protocol task object — wraps an internal task run."""

    id: str = Field(default_factory=_new_id)
    session_id: str | None = Field(None, alias="sessionId")
    context_id: str | None = Field(None, alias="contextId")
    status: A2ATaskStatus
    artifacts: list[A2AArtifact] = Field(default_factory=list)
    history: list[A2AMessage] = Field(default_factory=list)
    metadata: dict[str, Any] | None = None
    model_config = ConfigDict(populate_by_name=True)


# ===========================================================================
# DB Pydantic Schemas  (from_attributes=True for ORM ↔ Pydantic round-trips)
# ===========================================================================

class ArtifactSchema(BaseModel):
    """Pydantic projection of ArtifactModel.

    The ``metadata`` field uses ``alias='metadata_'`` so that Pydantic reads
    the ORM column attribute (``model.metadata_``) rather than SQLAlchemy's
    class-level ``DeclarativeBase.metadata`` (a ``MetaData`` object).
    ``populate_by_name=True`` lets callers still pass ``metadata=...`` as a
    keyword argument when constructing schemas in Python.
    """

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
    )

    id: str = Field(default_factory=_new_id)
    task_run_id: str
    name: str
    artifact_type: str
    location: str | None = None
    value: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    # alias='metadata_' → reads obj.metadata_ from ORM; populate_by_name lets
    # callers use metadata=... in Python constructors.
    metadata: dict[str, Any] | None = Field(
        None, alias="metadata_", serialization_alias="metadata"
    )

    def to_a2a(self) -> A2AArtifact:
        """Convert to an A2A artifact for inter-agent communication."""
        parts: list[Part] = []
        if self.value:
            parts.append(DataPart(data=self.value))
        if self.location:
            parts.append(FilePart(file=FileInfo(uri=self.location)))
        return A2AArtifact(name=self.name, parts=parts, metadata=self.metadata)


class TaskRunSchema(BaseModel):
    """Pydantic projection of TaskRunModel."""

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
    )

    id: str = Field(default_factory=_new_id)
    workflow_run_id: str
    task_name: str
    task_type: str
    status: TaskStatus = TaskStatus.PENDING
    started_at: datetime = Field(default_factory=_utcnow)
    completed_at: datetime | None = None
    error_message: str | None = None
    metadata: dict[str, Any] | None = Field(
        None, alias="metadata_", serialization_alias="metadata"
    )
    artifacts: list[ArtifactSchema] = Field(default_factory=list)

    def to_a2a_task(self) -> A2ATask:
        """Convert this task run to an A2A Task object."""
        a2a_state = A2ATaskState.from_task_status(self.status)
        status_msg: A2AMessage | None = None
        if self.error_message:
            status_msg = A2AMessage.text(role="agent", text=self.error_message)
        return A2ATask(
            id=self.id,
            status=A2ATaskStatus(state=a2a_state, message=status_msg),
            artifacts=[art.to_a2a() for art in self.artifacts],
            metadata=self.metadata,
        )


class WorkflowRunSchema(BaseModel):
    """Pydantic projection of WorkflowRunModel."""

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
    )

    id: str = Field(default_factory=_new_id)
    workflow_name: str
    status: TaskStatus = TaskStatus.PENDING
    started_at: datetime = Field(default_factory=_utcnow)
    completed_at: datetime | None = None
    metadata: dict[str, Any] | None = Field(
        None, alias="metadata_", serialization_alias="metadata"
    )
    task_runs: list[TaskRunSchema] = Field(default_factory=list)
