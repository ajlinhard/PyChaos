"""SQLAlchemy 2.x ORM models for workflow / task / artifact persistence.

Table groups
------------
Process logs  : workflow_runs, task_runs
Metadata      : workflow_metadata, task_metadata
A2A audit     : task_status_log, task_messages
Registries    : ai_models, prompts
Outputs       : artifacts
"""

from __future__ import annotations

import uuid
import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TaskStatus(str, enum.Enum):
    """Internal task/workflow execution states."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELED = "canceled"


class AIModelType(str, enum.Enum):
    """Broad capability category of an AI model."""

    LLM = "llm"
    EMBEDDING = "embedding"
    MULTIMODAL = "multimodal"
    OTHER = "other"


class PromptType(str, enum.Enum):
    """How the prompt is intended to be used by pydantic-ai / the agent."""

    SYSTEM = "system"       # maps to SystemPromptPart
    USER = "user"           # maps to UserPromptPart
    TEMPLATE = "template"   # user-prompt with {variable} placeholders
    FEW_SHOT = "few_shot"   # multi-message example collection


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Registry: ai_models
#   Aligned with pydantic-ai's KnownModelName and ModelSettings TypedDict.
# ---------------------------------------------------------------------------

class AIModelModel(Base):
    """Registry entry for an AI model (LLM, embedding, multimodal, etc.).

    ``pydantic_model_name`` holds the full pydantic-ai model identifier
    (e.g. ``"openai:gpt-4o"``, ``"anthropic:claude-3-5-sonnet-latest"``)
    so any task can instantiate the model via pydantic-ai directly.

    ``model_settings`` stores a JSON snapshot of pydantic-ai's
    ``ModelSettings`` TypedDict (temperature, max_tokens, top_p, seed, …).
    """

    __tablename__ = "ai_models"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    model_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # Full pydantic-ai model string — e.g. "openai:gpt-4o"
    pydantic_model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model_type: Mapped[AIModelType] = mapped_column(
        SAEnum(AIModelType, name="ai_model_type_enum", create_type=True),
        nullable=False,
    )
    # Token context window (LLMs) or vector dimensionality (embeddings)
    context_window: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dimensions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # JSON snapshot of pydantic_ai.settings.ModelSettings keys
    model_settings: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Provider-specific capability flags (function_calling, vision, etc.)
    capabilities: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    prompts: Mapped[list[PromptModel]] = relationship(back_populates="ai_model")
    task_runs: Mapped[list[TaskRunModel]] = relationship(back_populates="ai_model")
    artifacts: Mapped[list[ArtifactModel]] = relationship(back_populates="ai_model")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AIModel id={self.id!r} name={self.name!r}"
            f" type={self.model_type} provider={self.provider!r}>"
        )


# ---------------------------------------------------------------------------
# Registry: prompts
#   Structured around pydantic-ai's message types:
#     system_prompt → SystemPromptPart.content
#     user_template → UserPromptPart.content (with {variable} placeholders)
#     messages     → list[ModelRequest | ModelResponse] for few-shot history
#     dynamic_ref  → SystemPromptPart.dynamic_ref for dynamic prompts
# ---------------------------------------------------------------------------

class PromptModel(Base):
    """Versioned prompt template aligned with pydantic-ai message structure."""

    __tablename__ = "prompts"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False, default="1.0")
    prompt_type: Mapped[PromptType] = mapped_column(
        SAEnum(PromptType, name="prompt_type_enum", create_type=True),
        nullable=False,
        default=PromptType.TEMPLATE,
    )
    # System prompt text (SystemPromptPart.content)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # User prompt template with {variable} placeholders (UserPromptPart.content)
    user_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON list of variable placeholder names used in user_template
    variables: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # pydantic-ai compatible message history (few-shot examples / chain-of-thought)
    # Serialised list of ModelRequest / ModelResponse dataclass dicts
    messages: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    # Reference key for dynamic system prompt functions (SystemPromptPart.dynamic_ref)
    dynamic_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # Preferred AI model for this prompt
    model_id: Mapped[str | None] = mapped_column(
        ForeignKey("ai_models.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    ai_model: Mapped[AIModelModel | None] = relationship(back_populates="prompts")
    task_runs: Mapped[list[TaskRunModel]] = relationship(back_populates="prompt")
    artifacts: Mapped[list[ArtifactModel]] = relationship(back_populates="prompt")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Prompt id={self.id!r} name={self.name!r}"
            f" type={self.prompt_type} version={self.version!r}>"
        )


# ---------------------------------------------------------------------------
# Process log: workflow_runs
#   Pure execution state — no configuration blob.
#   Configuration lives in workflow_metadata (1:1).
# ---------------------------------------------------------------------------

class WorkflowRunModel(Base):
    """Execution log for one named workflow run (process log only)."""

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
    # FastA2A / Google A2A correlation identifiers
    a2a_context_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True, index=True
    )
    a2a_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    metadata_record: Mapped[WorkflowMetadataModel | None] = relationship(
        back_populates="workflow_run", uselist=False, cascade="all, delete-orphan"
    )
    task_runs: Mapped[list[TaskRunModel]] = relationship(
        back_populates="workflow_run", cascade="all, delete-orphan"
    )
    artifacts: Mapped[list[ArtifactModel]] = relationship(
        back_populates="workflow_run",
        primaryjoin="and_(ArtifactModel.workflow_run_id == WorkflowRunModel.id,"
                    " ArtifactModel.task_run_id == None)",
        viewonly=True,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<WorkflowRun id={self.id!r} name={self.workflow_name!r}"
            f" status={self.status}>"
        )


# ---------------------------------------------------------------------------
# Metadata: workflow_metadata  (1:1 with workflow_runs)
# ---------------------------------------------------------------------------

class WorkflowMetadataModel(Base):
    """Configuration and context for a workflow run, separate from process log."""

    __tablename__ = "workflow_metadata"

    __table_args__ = (
        UniqueConstraint("workflow_run_id", name="uq_workflow_metadata_run"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    workflow_run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # ["etl", "daily", "prod"]
    tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # {"env": "prod", "team": "ml", "owner": "alice"}
    labels: Mapped[dict[str, str] | None] = mapped_column(JSON, nullable=True)
    # Caller-supplied workflow configuration / run parameters
    config: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    workflow_run: Mapped[WorkflowRunModel] = relationship(
        back_populates="metadata_record"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<WorkflowMetadata run={self.workflow_run_id!r}>"


# ---------------------------------------------------------------------------
# Process log: task_runs
#   Pure execution state — no configuration blob.
#   Configuration lives in task_metadata (1:1).
# ---------------------------------------------------------------------------

class TaskRunModel(Base):
    """Execution log for one task within a workflow run (process log only)."""

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
    # Position of this task in the workflow sequence (0-indexed)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # FastA2A correlation
    a2a_context_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # AI infrastructure used during execution
    model_id: Mapped[str | None] = mapped_column(
        ForeignKey("ai_models.id", ondelete="SET NULL"), nullable=True, index=True
    )
    prompt_id: Mapped[str | None] = mapped_column(
        ForeignKey("prompts.id", ondelete="SET NULL"), nullable=True, index=True
    )

    workflow_run: Mapped[WorkflowRunModel] = relationship(back_populates="task_runs")
    ai_model: Mapped[AIModelModel | None] = relationship(back_populates="task_runs")
    prompt: Mapped[PromptModel | None] = relationship(back_populates="task_runs")
    metadata_record: Mapped[TaskMetadataModel | None] = relationship(
        back_populates="task_run", uselist=False, cascade="all, delete-orphan"
    )
    status_log: Mapped[list[TaskStatusLogModel]] = relationship(
        back_populates="task_run", cascade="all, delete-orphan", order_by="TaskStatusLogModel.timestamp"
    )
    messages: Mapped[list[TaskMessageModel]] = relationship(
        back_populates="task_run", cascade="all, delete-orphan", order_by="TaskMessageModel.sequence_number"
    )
    artifacts: Mapped[list[ArtifactModel]] = relationship(
        back_populates="task_run", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<TaskRun id={self.id!r} name={self.task_name!r}"
            f" type={self.task_type!r} status={self.status}>"
        )


# ---------------------------------------------------------------------------
# Metadata: task_metadata  (1:1 with task_runs)
# ---------------------------------------------------------------------------

class TaskMetadataModel(Base):
    """Configuration and context for a task run, separate from process log."""

    __tablename__ = "task_metadata"

    __table_args__ = (
        UniqueConstraint("task_run_id", name="uq_task_metadata_run"),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    task_run_id: Mapped[str] = mapped_column(
        ForeignKey("task_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    labels: Mapped[dict[str, str] | None] = mapped_column(JSON, nullable=True)
    # Task initialisation config passed at construction time
    config: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # JSON Schema describing the task's expected inputs
    input_schema: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # JSON Schema describing the task's expected outputs
    output_schema: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    task_run: Mapped[TaskRunModel] = relationship(back_populates="metadata_record")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TaskMetadata run={self.task_run_id!r}>"


# ---------------------------------------------------------------------------
# A2A audit: task_status_log
#   Records every A2A state transition for a task run.
#   Aligned with the FastA2A TaskStatus object:
#     { state, message: A2AMessage | None, timestamp }
# ---------------------------------------------------------------------------

class TaskStatusLogModel(Base):
    """Immutable audit log of A2A state transitions for a task run."""

    __tablename__ = "task_status_log"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    task_run_id: Mapped[str] = mapped_column(
        ForeignKey("task_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # A2A state string: submitted | working | completed | failed | canceled | input-required
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    # Optional A2A message associated with this status (e.g. error detail)
    message: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    task_run: Mapped[TaskRunModel] = relationship(back_populates="status_log")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<TaskStatusLog task={self.task_run_id!r} state={self.state!r}"
            f" at={self.timestamp.isoformat()}>"
        )


# ---------------------------------------------------------------------------
# A2A audit: task_messages
#   Persists the A2A message history (task.history) for a task run.
#   Aligned with the FastA2A Message object:
#     { role, parts: Part[], messageId, taskId, contextId, metadata }
# ---------------------------------------------------------------------------

class TaskMessageModel(Base):
    """A2A message history record for a task run."""

    __tablename__ = "task_messages"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    task_run_id: Mapped[str] = mapped_column(
        ForeignKey("task_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # A2A role: "user" | "agent"
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    # JSON array of A2A Part objects: [{type: "text", text: "..."}, ...]
    parts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    # A2A messageId — uniquely identifies this message
    message_id: Mapped[str] = mapped_column(
        String(36), nullable=False, default=lambda: str(uuid.uuid4())
    )
    # A2A contextId propagated from the calling agent
    context_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Ordinal position within this task's message history
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    extra_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    task_run: Mapped[TaskRunModel] = relationship(back_populates="messages")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<TaskMessage id={self.message_id!r} role={self.role!r}"
            f" seq={self.sequence_number}>"
        )


# ---------------------------------------------------------------------------
# Outputs: artifacts
#   Aligned with the FastA2A Artifact object:
#     { name, description, parts: Part[], index, append, lastChunk, metadata }
#   Also supports pre-workflow (workflow-scoped) artifacts via nullable task_run_id.
# ---------------------------------------------------------------------------

class ArtifactModel(Base):
    """Output artifact — task-scoped, workflow-scoped, or both.

    At least one of ``task_run_id`` / ``workflow_run_id`` must be non-null
    (DB check constraint).  Workflow-scoped artifacts (``task_run_id=None``)
    represent inputs/outputs registered before any task executes.

    ``parts`` is the canonical FastA2A representation (JSON array of Part
    objects).  ``value`` and ``location`` are convenience projections for
    quick queries without parsing the parts JSON.
    """

    __tablename__ = "artifacts"

    __table_args__ = (
        CheckConstraint(
            "task_run_id IS NOT NULL OR workflow_run_id IS NOT NULL",
            name="artifact_must_have_parent",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    task_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("task_runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    workflow_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Internal category tag (e.g. "file", "metric", "dataset", "embedding")
    artifact_type: Mapped[str] = mapped_column(String(128), nullable=False)
    # FastA2A canonical representation — list of Part objects
    # [{type: "text"|"data"|"file", ...part-specific fields}]
    parts: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    # FastA2A streaming / ordering fields
    index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    append: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_chunk: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Convenience projections (extracted from parts for direct querying)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    value: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Lineage: which model/prompt produced this artifact
    model_id: Mapped[str | None] = mapped_column(
        ForeignKey("ai_models.id", ondelete="SET NULL"), nullable=True
    )
    prompt_id: Mapped[str | None] = mapped_column(
        ForeignKey("prompts.id", ondelete="SET NULL"), nullable=True
    )
    extra_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    task_run: Mapped[TaskRunModel | None] = relationship(back_populates="artifacts")
    workflow_run: Mapped[WorkflowRunModel | None] = relationship(
        back_populates="artifacts",
        foreign_keys=[workflow_run_id],
        overlaps="artifacts",
    )
    ai_model: Mapped[AIModelModel | None] = relationship(back_populates="artifacts")
    prompt: Mapped[PromptModel | None] = relationship(back_populates="artifacts")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Artifact id={self.id!r} name={self.name!r}"
            f" type={self.artifact_type!r} idx={self.index}>"
        )
