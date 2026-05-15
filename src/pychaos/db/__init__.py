"""Database layer — models, schemas, and handler."""

from .handler import DatabaseHandler, DatabaseSettings
from .models import (
    AIModelModel,
    AIModelType,
    ArtifactModel,
    Base,
    PromptModel,
    PromptType,
    TaskMessageModel,
    TaskMetadataModel,
    TaskRunModel,
    TaskStatus,
    TaskStatusLogModel,
    WorkflowMetadataModel,
    WorkflowRunModel,
)
from .schema import (
    A2AArtifact,
    A2AMessage,
    A2ATask,
    A2ATaskState,
    A2ATaskStatus,
    AIModelSchema,
    ArtifactSchema,
    DataPart,
    FilePart,
    ModelSettings,
    Part,
    PromptSchema,
    TaskMessageSchema,
    TaskMetadataSchema,
    TaskRunSchema,
    TaskStatusLogSchema,
    TextPart,
    WorkflowMetadataSchema,
    WorkflowRunSchema,
)

__all__ = [
    # ORM models
    "Base",
    "AIModelModel",
    "AIModelType",
    "PromptModel",
    "PromptType",
    "WorkflowRunModel",
    "WorkflowMetadataModel",
    "TaskRunModel",
    "TaskMetadataModel",
    "TaskStatusLogModel",
    "TaskMessageModel",
    "ArtifactModel",
    "TaskStatus",
    # Handler
    "DatabaseSettings",
    "DatabaseHandler",
    # pydantic-ai re-export
    "ModelSettings",
    # DB schemas
    "AIModelSchema",
    "PromptSchema",
    "WorkflowRunSchema",
    "WorkflowMetadataSchema",
    "TaskRunSchema",
    "TaskMetadataSchema",
    "TaskStatusLogSchema",
    "TaskMessageSchema",
    "ArtifactSchema",
    # A2A Part types
    "TextPart",
    "DataPart",
    "FilePart",
    "Part",
    # A2A types
    "A2AMessage",
    "A2ATaskState",
    "A2ATaskStatus",
    "A2AArtifact",
    "A2ATask",
]
