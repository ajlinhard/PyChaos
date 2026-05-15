"""Pydantic v2 schemas for DB models and A2A / pydantic-ai protocol types.

Schema groups
-------------
A2A types       : TextPart, DataPart, FilePart, Part, A2AMessage,
                  A2ATaskState, A2ATaskStatus, A2AArtifact, A2ATask
pydantic-ai     : ModelSettings (re-exported)
DB schemas      : AIModelSchema, PromptSchema,
                  WorkflowRunSchema, WorkflowMetadataSchema,
                  TaskRunSchema, TaskMetadataSchema,
                  TaskStatusLogSchema, TaskMessageSchema,
                  ArtifactSchema
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from pydantic_ai.settings import ModelSettings  # re-exported for callers

from .models import AIModelType, PromptType, TaskStatus

__all__ = [
    # A2A protocol types (FastA2A / Google A2A spec)
    "TextPart",
    "DataPart",
    "FilePart",
    "Part",
    "A2AMessage",
    "A2ATaskState",
    "A2ATaskStatus",
    "A2AArtifact",
    "A2ATask",
    # pydantic-ai re-exports
    "ModelSettings",
    # Enum re-exports
    "AIModelType",
    "PromptType",
    "TaskStatus",
    # DB schemas
    "AIModelSchema",
    "PromptSchema",
    "WorkflowMetadataSchema",
    "WorkflowRunSchema",
    "TaskMetadataSchema",
    "TaskStatusLogSchema",
    "TaskMessageSchema",
    "TaskRunSchema",
    "ArtifactSchema",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_id() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ===========================================================================
# A2A Protocol types  (FastA2A / Google A2A spec)
# ===========================================================================

class TextPart(BaseModel):
    """Plain-text content part."""

    type: Literal["text"] = "text"
    text: str
    metadata: dict[str, Any] | None = None


class DataPart(BaseModel):
    """Structured JSON data content part."""

    type: Literal["data"] = "data"
    data: dict[str, Any]
    metadata: dict[str, Any] | None = None


class FileInfo(BaseModel):
    uri: str
    mime_type: str | None = Field(None, alias="mimeType")
    name: str | None = None
    model_config = ConfigDict(populate_by_name=True)


class FilePart(BaseModel):
    """File-reference content part."""

    type: Literal["file"] = "file"
    file: FileInfo
    metadata: dict[str, Any] | None = None


# Discriminated union — Pydantic resolves from the "type" field
Part = Annotated[
    Union[TextPart, DataPart, FilePart],
    Field(discriminator="type"),
]

# Module-level adapter so TypeAdapter isn't rebuilt on every call
_part_adapter: TypeAdapter[Part] = TypeAdapter(Part)


class A2AMessage(BaseModel):
    """A2A protocol message (user ↔ agent exchange)."""

    role: Literal["user", "agent"]
    parts: list[Part] = Field(default_factory=list)
    message_id: str = Field(default_factory=_new_id, alias="messageId")
    task_id: str | None = Field(None, alias="taskId")
    context_id: str | None = Field(None, alias="contextId")
    metadata: dict[str, Any] | None = None
    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def text(
        cls, role: Literal["user", "agent"], text: str, **kwargs: Any
    ) -> "A2AMessage":
        return cls(role=role, parts=[TextPart(text=text)], **kwargs)

    @classmethod
    def data(
        cls, role: Literal["user", "agent"], payload: dict[str, Any], **kwargs: Any
    ) -> "A2AMessage":
        return cls(role=role, parts=[DataPart(data=payload)], **kwargs)


class A2ATaskState(str, Enum):
    """A2A task lifecycle states (FastA2A spec)."""

    SUBMITTED = "submitted"
    WORKING = "working"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    INPUT_REQUIRED = "input-required"

    @classmethod
    def from_task_status(cls, status: TaskStatus) -> "A2ATaskState":
        _map: dict[TaskStatus, A2ATaskState] = {
            TaskStatus.PENDING: cls.SUBMITTED,
            TaskStatus.RUNNING: cls.WORKING,
            TaskStatus.COMPLETED: cls.COMPLETED,
            TaskStatus.FAILED: cls.FAILED,
            TaskStatus.CANCELED: cls.CANCELED,
            TaskStatus.SKIPPED: cls.CANCELED,
        }
        return _map[status]


class A2ATaskStatus(BaseModel):
    """Current state of an A2A task (FastA2A TaskStatus object)."""

    state: A2ATaskState
    message: A2AMessage | None = None
    timestamp: datetime = Field(default_factory=_utcnow)


class A2AArtifact(BaseModel):
    """Output artifact produced by an A2A task (FastA2A Artifact object).

    Field names mirror the FastA2A spec exactly:
        name, description, parts, index, append, lastChunk, metadata
    """

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
        return cls(name=name, parts=[DataPart(data=value)], **kwargs)

    @classmethod
    def from_file(
        cls, name: str, uri: str, mime_type: str | None = None, **kwargs: Any
    ) -> "A2AArtifact":
        return cls(
            name=name,
            parts=[FilePart(file=FileInfo(uri=uri, mime_type=mime_type))],
            **kwargs,
        )


class A2ATask(BaseModel):
    """A2A Task object (FastA2A spec).

    Represents either a single task run or an entire workflow run
    when exposed to external agents.
    """

    id: str = Field(default_factory=_new_id)
    session_id: str | None = Field(None, alias="sessionId")
    context_id: str | None = Field(None, alias="contextId")
    status: A2ATaskStatus
    artifacts: list[A2AArtifact] = Field(default_factory=list)
    history: list[A2AMessage] = Field(default_factory=list)
    metadata: dict[str, Any] | None = None
    model_config = ConfigDict(populate_by_name=True)


# ===========================================================================
# DB Pydantic Schemas  (defined in dependency order — no forward references)
# ===========================================================================

# ---------------------------------------------------------------------------
# Registry schemas
# ---------------------------------------------------------------------------

class AIModelSchema(BaseModel):
    """Pydantic projection of AIModelModel.

    ``pydantic_model_name`` holds the full pydantic-ai model string
    (``"openai:gpt-4o"``, ``"anthropic:claude-3-5-sonnet-latest"``, …) so
    any task can instantiate the model via pydantic-ai.

    ``model_settings`` is a dict whose keys match
    ``pydantic_ai.settings.ModelSettings`` (temperature, max_tokens, top_p,
    seed, …) and is persisted as a JSON column.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str = Field(default_factory=_new_id)
    name: str
    provider: str
    model_id: str
    pydantic_model_name: str | None = None
    model_type: AIModelType
    version: str | None = None
    context_window: int | None = None
    dimensions: int | None = None
    # Dict whose keys match pydantic_ai.settings.ModelSettings (temperature,
    # max_tokens, top_p, seed, …).  Stored as JSON; use ModelSettings TypedDict
    # for static-typing when constructing this schema in Python.
    model_settings: dict[str, Any] | None = None
    capabilities: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    extra_metadata: dict[str, Any] | None = None


class PromptSchema(BaseModel):
    """Pydantic projection of PromptModel.

    Fields map to pydantic-ai message types:
    - ``system_prompt`` → ``SystemPromptPart.content``
    - ``user_template``  → ``UserPromptPart.content`` (with {var} placeholders)
    - ``messages``       → list of serialised ModelRequest / ModelResponse dicts
    - ``dynamic_ref``    → ``SystemPromptPart.dynamic_ref``
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str = Field(default_factory=_new_id)
    name: str
    version: str = "1.0"
    prompt_type: PromptType = PromptType.TEMPLATE
    system_prompt: str | None = None
    user_template: str | None = None
    variables: list[str] | None = None
    messages: list[dict[str, Any]] | None = None
    dynamic_ref: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    model_id: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)

    def render_user(self, **variables: Any) -> str:
        """Render ``user_template`` by substituting ``{variable}`` placeholders."""
        if self.user_template is None:
            raise ValueError(f"Prompt {self.name!r} has no user_template to render.")
        return self.user_template.format(**variables)

    def render_system(self) -> str:
        """Return the static ``system_prompt`` text."""
        if self.system_prompt is None:
            raise ValueError(f"Prompt {self.name!r} has no system_prompt.")
        return self.system_prompt


# ---------------------------------------------------------------------------
# Metadata schemas (separate from process log)
# ---------------------------------------------------------------------------

class WorkflowMetadataSchema(BaseModel):
    """Configuration context for a workflow run — separate from the process log."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str = Field(default_factory=_new_id)
    workflow_run_id: str
    description: str | None = None
    version: str | None = None
    tags: list[str] | None = None
    labels: dict[str, str] | None = None
    config: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class TaskMetadataSchema(BaseModel):
    """Configuration context for a task run — separate from the process log."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str = Field(default_factory=_new_id)
    task_run_id: str
    description: str | None = None
    tags: list[str] | None = None
    labels: dict[str, str] | None = None
    config: dict[str, Any] | None = None
    # JSON Schema describing the task's expected inputs / outputs
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# A2A audit schemas
# ---------------------------------------------------------------------------

class TaskStatusLogSchema(BaseModel):
    """A2A state-transition audit record (FastA2A TaskStatus snapshot)."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str = Field(default_factory=_new_id)
    task_run_id: str
    # A2A state: submitted | working | completed | failed | canceled | input-required
    state: str
    message: dict[str, Any] | None = None
    timestamp: datetime = Field(default_factory=_utcnow)

    @classmethod
    def from_status(
        cls,
        task_run_id: str,
        status: TaskStatus,
        message: A2AMessage | None = None,
    ) -> "TaskStatusLogSchema":
        """Build a log entry from an internal TaskStatus transition."""
        return cls(
            task_run_id=task_run_id,
            state=A2ATaskState.from_task_status(status).value,
            message=message.model_dump(by_alias=True) if message else None,
        )


class TaskMessageSchema(BaseModel):
    """A2A message history record for a task run (FastA2A Message object)."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str = Field(default_factory=_new_id)
    task_run_id: str
    role: Literal["user", "agent"]
    parts: list[dict[str, Any]] = Field(default_factory=list)
    message_id: str = Field(default_factory=_new_id)
    context_id: str | None = None
    sequence_number: int = 0
    created_at: datetime = Field(default_factory=_utcnow)
    extra_metadata: dict[str, Any] | None = None

    @classmethod
    def from_a2a(
        cls, task_run_id: str, msg: A2AMessage, sequence_number: int = 0
    ) -> "TaskMessageSchema":
        """Create a DB record from an A2AMessage."""
        return cls(
            task_run_id=task_run_id,
            role=msg.role,
            parts=[p.model_dump(by_alias=True) for p in msg.parts],
            message_id=msg.message_id,
            context_id=msg.context_id,
            sequence_number=sequence_number,
        )

    def to_a2a(self) -> A2AMessage:
        """Reconstitute an A2AMessage from this DB record."""
        parts = [_part_adapter.validate_python(p) for p in self.parts]
        return A2AMessage(
            role=self.role,
            parts=parts,
            message_id=self.message_id,
            context_id=self.context_id,
        )


# ---------------------------------------------------------------------------
# Artifact schema — aligned with FastA2A Artifact object
# ---------------------------------------------------------------------------

class ArtifactSchema(BaseModel):
    """Pydantic projection of ArtifactModel.

    ``parts`` is the canonical FastA2A representation (JSON array of Part
    objects).  ``value`` and ``location`` are convenience projections for
    quick queries without parsing parts.

    ``task_run_id`` is nullable — workflow-scoped (pre-task) artifacts carry
    only ``workflow_run_id``.  At least one must be provided.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str = Field(default_factory=_new_id)
    task_run_id: str | None = None
    workflow_run_id: str | None = None
    name: str
    description: str | None = None
    artifact_type: str
    # FastA2A canonical Part list: [{type: "text"|"data"|"file", ...}]
    parts: list[dict[str, Any]] | None = None
    # FastA2A streaming / ordering fields
    index: int = 0
    append: bool = False
    last_chunk: bool = True
    # Convenience projections (extracted from parts for direct SQL filtering)
    location: str | None = None
    value: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    model_id: str | None = None
    prompt_id: str | None = None
    extra_metadata: dict[str, Any] | None = None

    def to_a2a(self) -> A2AArtifact:
        """Convert to a FastA2A Artifact object."""
        if self.parts:
            typed_parts: list[Part] = [
                _part_adapter.validate_python(p) for p in self.parts
            ]
        else:
            typed_parts = []
            if self.value:
                typed_parts.append(DataPart(data=self.value))
            if self.location:
                typed_parts.append(FilePart(file=FileInfo(uri=self.location)))

        return A2AArtifact(
            name=self.name,
            description=self.description,
            parts=typed_parts,
            index=self.index,
            append=self.append,
            last_chunk=self.last_chunk,
            metadata=self.extra_metadata,
        )


# ---------------------------------------------------------------------------
# Process-log schemas — no config blobs; metadata lives in separate schemas
# ---------------------------------------------------------------------------

class TaskRunSchema(BaseModel):
    """Pydantic projection of TaskRunModel (process log only).

    Configuration is in the nested ``metadata`` field (TaskMetadataSchema).
    State-transition history is in ``status_log``.
    Message history is in ``messages``.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str = Field(default_factory=_new_id)
    workflow_run_id: str
    task_name: str
    task_type: str
    status: TaskStatus = TaskStatus.PENDING
    started_at: datetime = Field(default_factory=_utcnow)
    completed_at: datetime | None = None
    error_message: str | None = None
    sequence_number: int = 0
    a2a_context_id: str | None = None
    model_id: str | None = None
    prompt_id: str | None = None

    metadata: TaskMetadataSchema | None = None
    status_log: list[TaskStatusLogSchema] = Field(default_factory=list)
    messages: list[TaskMessageSchema] = Field(default_factory=list)
    artifacts: list[ArtifactSchema] = Field(default_factory=list)

    def to_a2a_task(self) -> A2ATask:
        """Convert this task run to a FastA2A Task object."""
        status_msg: A2AMessage | None = None
        if self.error_message:
            status_msg = A2AMessage.text(role="agent", text=self.error_message)
        return A2ATask(
            id=self.id,
            context_id=self.a2a_context_id,
            status=A2ATaskStatus(
                state=A2ATaskState.from_task_status(self.status),
                message=status_msg,
            ),
            artifacts=[art.to_a2a() for art in self.artifacts],
            history=[msg.to_a2a() for msg in self.messages],
        )


class WorkflowRunSchema(BaseModel):
    """Pydantic projection of WorkflowRunModel (process log only).

    Configuration is in the nested ``metadata`` field (WorkflowMetadataSchema).
    Pre-workflow artifacts are in ``pre_artifacts``.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str = Field(default_factory=_new_id)
    workflow_name: str
    status: TaskStatus = TaskStatus.PENDING
    started_at: datetime = Field(default_factory=_utcnow)
    completed_at: datetime | None = None
    a2a_context_id: str | None = None
    a2a_session_id: str | None = None

    metadata: WorkflowMetadataSchema | None = None
    task_runs: list[TaskRunSchema] = Field(default_factory=list)
    pre_artifacts: list[ArtifactSchema] = Field(default_factory=list)

    def to_a2a_task(self) -> A2ATask:
        """Represent this entire workflow run as a FastA2A Task."""
        all_artifacts = [art.to_a2a() for art in self.pre_artifacts]
        history: list[A2AMessage] = []
        for tr in self.task_runs:
            all_artifacts.extend(art.to_a2a() for art in tr.artifacts)
            history.extend(msg.to_a2a() for msg in tr.messages)
        return A2ATask(
            id=self.id,
            session_id=self.a2a_session_id,
            context_id=self.a2a_context_id,
            status=A2ATaskStatus(state=A2ATaskState.from_task_status(self.status)),
            artifacts=all_artifacts,
            history=history,
        )
