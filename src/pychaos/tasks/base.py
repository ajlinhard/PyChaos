"""BaseTask — abstract base for all PyChaos tasks.

Process state is persisted to ``task_runs`` (no config blob).
Configuration is persisted to ``task_metadata`` (1:1, separate table).
Every status transition is appended to ``task_status_log`` (A2A audit).

Example::

    @register_task
    class SumTask(BaseTask):
        task_type = "sum"

        def init_context(self, context=None) -> None:
            numbers = self.config.get("numbers", [])
            if not isinstance(numbers, list):
                raise ValueError("config.numbers must be a list")
            self.task_context["numbers"] = numbers

        def process(self, context=None) -> None:
            total = sum(self.task_context["numbers"])
            self.output["total"] = total
            self.store_artifacts("sum_result", "metric", value={"total": total})

        def verify_output(self, context=None) -> None:
            assert "total" in self.output
"""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ..db.models import TaskStatus
from ..db.schema import (
    A2AMessage,
    ArtifactSchema,
    TaskMetadataSchema,
    TaskRunSchema,
    TaskStatusLogSchema,
)

if TYPE_CHECKING:
    from ..db.handler import DatabaseHandler
    from ..workflow.context import WorkflowContext

logger = logging.getLogger(__name__)


class BaseTask(ABC):
    """Abstract base class for all workflow tasks.

    Parameters
    ----------
    name:
        Human-readable label for this task instance.
    workflow_run_id:
        ID of the parent WorkflowRun; links DB rows.
    db_handler:
        Optional.  When provided, the task logs its run, metadata, status
        transitions, and artifacts to the database.
    config:
        Arbitrary key-value configuration — stored in ``task_metadata.config``
        (not in the process log row).
    model_id:
        ID of the AI model this task will use (logged to ``task_runs``).
    prompt_id:
        ID of the prompt this task will use (logged to ``task_runs``).
    tags:
        String tags stored in ``task_metadata``.
    labels:
        Key-value labels stored in ``task_metadata``.
    description:
        Human-readable description stored in ``task_metadata``.
    """

    #: Must be overridden in every concrete subclass
    task_type: str = "base"

    def __init__(
        self,
        name: str,
        workflow_run_id: str,
        db_handler: "DatabaseHandler | None" = None,
        config: dict[str, Any] | None = None,
        model_id: str | None = None,
        prompt_id: str | None = None,
        tags: list[str] | None = None,
        labels: dict[str, str] | None = None,
        description: str | None = None,
    ) -> None:
        self.name = name
        self.workflow_run_id = workflow_run_id
        self.db_handler = db_handler
        self.config: dict[str, Any] = config or {}
        self.model_id = model_id
        self.prompt_id = prompt_id
        self.task_run_id: str = str(uuid.uuid4())
        # Set by BaseWorkflow.run() before execute() is called
        self.sequence_number: int = 0

        # Internal scratch space: init_context writes here; process reads here.
        # Named task_context to avoid shadowing the WorkflowContext parameter.
        self.task_context: dict[str, Any] = {}
        # Populated by process(); inspected by verify_output()
        self.output: dict[str, Any] = {}
        # In-memory list of artifacts produced by this task
        self.artifacts: list[ArtifactSchema] = []

        self._task_run_schema: TaskRunSchema | None = None
        self._log_task_run_created(tags=tags, labels=labels, description=description)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(self, context: "WorkflowContext | None" = None) -> dict[str, Any]:
        """Orchestrate the task lifecycle.

        Calls ``init_context`` → ``process`` → ``verify_output`` in order,
        forwarding the shared :class:`WorkflowContext` to each step.

        Parameters
        ----------
        context:
            The workflow-level shared context; ``None`` in standalone / test use.

        Returns
        -------
        dict
            The contents of ``self.output`` after a successful run.
        """
        logger.info("Task [%s/%s] starting", self.task_type, self.name)
        self._update_status(TaskStatus.RUNNING)

        try:
            logger.debug("Task [%s] — init_context", self.name)
            self.init_context(context)

            logger.debug("Task [%s] — process", self.name)
            self.process(context)

            logger.debug("Task [%s] — verify_output", self.name)
            self.verify_output(context)

        except Exception as exc:
            logger.exception("Task [%s] failed: %s", self.name, exc)
            self._update_status(
                TaskStatus.FAILED,
                completed_at=datetime.now(timezone.utc),
                error_message=str(exc),
                a2a_message=A2AMessage.text(role="agent", text=str(exc)),
            )
            raise

        self._update_status(TaskStatus.COMPLETED, completed_at=datetime.now(timezone.utc))
        logger.info("Task [%s/%s] completed", self.task_type, self.name)
        return self.output

    # ------------------------------------------------------------------
    # Abstract sub-functions  (WorkflowContext placeholder)
    # ------------------------------------------------------------------

    @abstractmethod
    def init_context(self, context: "WorkflowContext | None" = None) -> None:
        """Initialise data required by the task and validate inputs.

        Populate ``self.task_context`` with everything ``process()`` needs.
        Read from ``context.pre_artifacts``, ``context.task_outputs``, or
        ``context.get_model()`` / ``context.get_prompt()`` to access shared
        workflow resources.

        Raise ``ValueError`` (or any exception) to abort early.
        """

    @abstractmethod
    def process(self, context: "WorkflowContext | None" = None) -> None:
        """Execute the core logic.

        Read from ``self.task_context``; write results to ``self.output``.
        Call ``self.store_artifacts(...)`` for notable outputs.
        Write to ``context.shared_state`` if downstream tasks need live data.
        """

    @abstractmethod
    def verify_output(self, context: "WorkflowContext | None" = None) -> None:
        """Assert that ``self.output`` meets expectations.

        Raise ``AssertionError`` (or any exception) for missing keys,
        out-of-range values, failed schema validation, etc.
        """

    # ------------------------------------------------------------------
    # Artifact helper
    # ------------------------------------------------------------------

    def store_artifacts(
        self,
        name: str,
        artifact_type: str,
        description: str | None = None,
        value: dict[str, Any] | None = None,
        location: str | None = None,
        parts: list[dict[str, Any]] | None = None,
        index: int = 0,
        append: bool = False,
        last_chunk: bool = True,
        model_id: str | None = None,
        prompt_id: str | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> ArtifactSchema:
        """Create and persist a task-scoped artifact record.

        Supports the full FastA2A ``parts`` list (canonical) or convenience
        ``value`` / ``location`` projections for simple cases.

        Parameters
        ----------
        name:
            Short identifier (e.g. ``"predictions_csv"``).
        artifact_type:
            Category string (e.g. ``"file"``, ``"metric"``, ``"dataset"``).
        description:
            Human-readable description (stored in FastA2A artifact).
        parts:
            FastA2A Part list: ``[{type: "text"|"data"|"file", ...}]``.
        index:
            FastA2A streaming index (position in artifact stream).
        append:
            FastA2A streaming append flag.
        last_chunk:
            FastA2A streaming last-chunk flag.
        value:
            Convenience: inline JSON payload (materialised as a DataPart).
        location:
            Convenience: URI / path (materialised as a FilePart).
        model_id:
            Overrides task-level ``self.model_id`` for this artifact.
        prompt_id:
            Overrides task-level ``self.prompt_id`` for this artifact.
        """
        artifact = ArtifactSchema(
            id=str(uuid.uuid4()),
            task_run_id=self.task_run_id,
            workflow_run_id=self.workflow_run_id,
            name=name,
            description=description,
            artifact_type=artifact_type,
            parts=parts,
            index=index,
            append=append,
            last_chunk=last_chunk,
            value=value,
            location=location,
            model_id=model_id or self.model_id,
            prompt_id=prompt_id or self.prompt_id,
            extra_metadata=extra_metadata,
            created_at=datetime.now(timezone.utc),
        )
        self.artifacts.append(artifact)

        if self.db_handler is not None:
            self.db_handler.log_artifact(artifact)

        logger.debug(
            "Artifact stored: name=%r type=%r task=%r", name, artifact_type, self.name
        )
        return artifact

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _log_task_run_created(
        self,
        tags: list[str] | None,
        labels: dict[str, str] | None,
        description: str | None,
    ) -> None:
        """Persist the initial PENDING task-run and its metadata rows."""
        now = datetime.now(timezone.utc)
        self._task_run_schema = TaskRunSchema(
            id=self.task_run_id,
            workflow_run_id=self.workflow_run_id,
            task_name=self.name,
            task_type=self.task_type,
            status=TaskStatus.PENDING,
            started_at=now,
            sequence_number=self.sequence_number,
            model_id=self.model_id,
            prompt_id=self.prompt_id,
        )
        if self.db_handler is not None:
            # Process log row — no config
            self.db_handler.log_task_run(self._task_run_schema)
            # Separate metadata row
            meta = TaskMetadataSchema(
                task_run_id=self.task_run_id,
                description=description,
                tags=tags,
                labels=labels,
                config=self.config or None,
                created_at=now,
            )
            self.db_handler.log_task_metadata(meta)
            # Initial A2A status log entry
            self.db_handler.log_task_status(
                TaskStatusLogSchema.from_status(self.task_run_id, TaskStatus.PENDING)
            )

    def _update_status(
        self,
        status: TaskStatus,
        completed_at: datetime | None = None,
        error_message: str | None = None,
        a2a_message: A2AMessage | None = None,
    ) -> None:
        if self.db_handler is not None:
            self.db_handler.update_task_status(
                task_run_id=self.task_run_id,
                status=status,
                completed_at=completed_at,
                error_message=error_message,
            )
            self.db_handler.log_task_status(
                TaskStatusLogSchema.from_status(self.task_run_id, status, a2a_message)
            )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<{self.__class__.__name__} name={self.name!r}"
            f" type={self.task_type!r} id={self.task_run_id!r}>"
        )
