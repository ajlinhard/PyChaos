"""BaseWorkflow — orchestrates an ordered sequence of tasks.

Usage::

    class MyWorkflow(BaseWorkflow):
        name = "etl_pipeline"

        def build(self) -> None:
            self.add_task(ExtractTask("extract", db_handler=self.db_handler))
            self.add_task(TransformTask("transform", db_handler=self.db_handler))

    wf = MyWorkflow(
        name="etl_pipeline",
        db_handler=db,
        config={"batch_size": 500},
        a2a_context_id="ctx-123",
        a2a_session_id="session-abc",
        tags=["etl", "daily"],
        labels={"env": "prod", "team": "ml"},
    )

    # Register pre-workflow (input) artifacts before running
    wf.register_pre_artifact("training_data", "dataset", location="s3://bucket/data.csv")

    results = wf.run()
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ..db.models import TaskStatus
from ..db.schema import (
    AIModelSchema,
    ArtifactSchema,
    PromptSchema,
    WorkflowMetadataSchema,
    WorkflowRunSchema,
)
from .context import WorkflowContext

if TYPE_CHECKING:
    from ..db.handler import DatabaseHandler
    from ..tasks.base import BaseTask

logger = logging.getLogger(__name__)


class BaseWorkflow:
    """Sequential workflow executor with FastA2A support.

    Process state is persisted to ``workflow_runs`` (no config blob).
    Configuration is persisted to ``workflow_metadata`` (1:1, separate table).

    Parameters
    ----------
    name:
        Logical name for this workflow (stored in the DB).
    db_handler:
        Optional.  When provided, runs are persisted.
    config:
        Arbitrary run parameters — stored in ``workflow_metadata.config``.
    a2a_context_id:
        FastA2A context identifier for cross-agent correlation.
    a2a_session_id:
        FastA2A session identifier for stateful multi-turn interactions.
    tags:
        List of string tags stored in ``workflow_metadata``.
    labels:
        Key-value label map stored in ``workflow_metadata``.
    description:
        Human-readable description stored in ``workflow_metadata``.
    version:
        Version string for this workflow definition.
    """

    def __init__(
        self,
        name: str,
        db_handler: "DatabaseHandler | None" = None,
        config: dict[str, Any] | None = None,
        a2a_context_id: str | None = None,
        a2a_session_id: str | None = None,
        tags: list[str] | None = None,
        labels: dict[str, str] | None = None,
        description: str | None = None,
        version: str | None = None,
    ) -> None:
        self.name = name
        self.db_handler = db_handler
        self.config: dict[str, Any] = config or {}
        self.workflow_run_id: str = str(uuid.uuid4())

        self._tasks: list["BaseTask"] = []
        self._results: dict[str, Any] = {}
        self._workflow_run_schema: WorkflowRunSchema | None = None

        self.context: WorkflowContext = WorkflowContext(
            workflow_run_id=self.workflow_run_id,
            a2a_context_id=a2a_context_id,
            a2a_session_id=a2a_session_id,
        )

        self._log_workflow_created(
            tags=tags, labels=labels, description=description, version=version
        )

    # ------------------------------------------------------------------
    # Pre-workflow artifact registration (rollup before tasks run)
    # ------------------------------------------------------------------

    def register_pre_artifact(
        self,
        name: str,
        artifact_type: str,
        description: str | None = None,
        value: dict[str, Any] | None = None,
        location: str | None = None,
        parts: list[dict[str, Any]] | None = None,
        model_id: str | None = None,
        prompt_id: str | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> ArtifactSchema:
        """Register a workflow-scoped (pre-task) artifact.

        Persisted with ``task_run_id=None`` and accessible to all tasks via
        ``context.pre_artifacts``.  Supports the full FastA2A ``parts`` list
        or convenience ``value`` / ``location`` projections.
        """
        artifact = ArtifactSchema(
            id=str(uuid.uuid4()),
            workflow_run_id=self.workflow_run_id,
            task_run_id=None,
            name=name,
            description=description,
            artifact_type=artifact_type,
            parts=parts,
            value=value,
            location=location,
            model_id=model_id,
            prompt_id=prompt_id,
            extra_metadata=extra_metadata,
            created_at=datetime.now(timezone.utc),
        )
        self.context.pre_artifacts.append(artifact)
        if self.db_handler is not None:
            self.db_handler.log_artifact(artifact)
        logger.debug("Pre-artifact registered: name=%r type=%r", name, artifact_type)
        return artifact

    # ------------------------------------------------------------------
    # AI infrastructure registration
    # ------------------------------------------------------------------

    def register_model(
        self, schema: AIModelSchema, persist: bool = True
    ) -> "BaseWorkflow":
        """Register an AI model in the workflow context.

        Parameters
        ----------
        schema:
            ``AIModelSchema`` describing the model.
        persist:
            When True (default), upsert into the ``ai_models`` registry table.
        """
        self.context.models[schema.name] = schema
        if persist and self.db_handler is not None:
            self.db_handler.log_ai_model(schema)
        return self

    def register_prompt(
        self, schema: PromptSchema, persist: bool = True
    ) -> "BaseWorkflow":
        """Register a prompt template in the workflow context.

        Parameters
        ----------
        schema:
            ``PromptSchema`` describing the prompt.
        persist:
            When True (default), upsert into the ``prompts`` registry table.
        """
        self.context.prompts[schema.name] = schema
        if persist and self.db_handler is not None:
            self.db_handler.log_prompt(schema)
        return self

    # ------------------------------------------------------------------
    # Task management
    # ------------------------------------------------------------------

    def add_task(self, task: "BaseTask") -> "BaseWorkflow":
        """Append a task to the execution queue.  Returns self for chaining."""
        self._tasks.append(task)
        return self

    def add_tasks(self, *tasks: "BaseTask") -> "BaseWorkflow":
        for task in tasks:
            self.add_task(task)
        return self

    @property
    def tasks(self) -> list["BaseTask"]:
        return list(self._tasks)

    # ------------------------------------------------------------------
    # Build hook
    # ------------------------------------------------------------------

    def build(self) -> None:
        """Override to construct the task list declaratively.

        Called automatically by :meth:`run` when the task list is empty.
        """

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        """Execute all tasks sequentially, passing the shared WorkflowContext.

        Each task receives ``self.context`` so it can access:
        - ``context.pre_artifacts``  — workflow-level inputs
        - ``context.task_outputs``   — outputs from previously completed tasks
        - ``context.shared_state``   — mutable cross-task state
        - ``context.models``         — registered AI models
        - ``context.prompts``        — registered prompt templates

        Returns
        -------
        dict
            Mapping of ``task.name → task.output`` for every completed task.
        """
        if not self._tasks:
            self.build()

        if not self._tasks:
            logger.warning("Workflow [%s] has no tasks.", self.name)
            self._update_status(TaskStatus.COMPLETED, completed_at=datetime.now(timezone.utc))
            return {}

        logger.info("Workflow [%s] starting — %d task(s)", self.name, len(self._tasks))
        self._update_status(TaskStatus.RUNNING)

        try:
            for idx, task in enumerate(self._tasks):
                task.sequence_number = idx
                output = task.execute(context=self.context)
                self._results[task.name] = output
                self.context.record_task_output(task.name, output)
        except Exception as exc:
            logger.error("Workflow [%s] failed: %s", self.name, exc)
            self._update_status(TaskStatus.FAILED, completed_at=datetime.now(timezone.utc))
            raise

        self._update_status(TaskStatus.COMPLETED, completed_at=datetime.now(timezone.utc))
        logger.info("Workflow [%s] completed.", self.name)
        return dict(self._results)

    # ------------------------------------------------------------------
    # Result access
    # ------------------------------------------------------------------

    @property
    def results(self) -> dict[str, Any]:
        return dict(self._results)

    def task_output(self, task_name: str) -> Any:
        return self._results.get(task_name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _log_workflow_created(
        self,
        tags: list[str] | None,
        labels: dict[str, str] | None,
        description: str | None,
        version: str | None,
    ) -> None:
        now = datetime.now(timezone.utc)
        self._workflow_run_schema = WorkflowRunSchema(
            id=self.workflow_run_id,
            workflow_name=self.name,
            status=TaskStatus.PENDING,
            started_at=now,
            a2a_context_id=self.context.a2a_context_id,
            a2a_session_id=self.context.a2a_session_id,
        )
        if self.db_handler is not None:
            # Process log row — no config
            self.db_handler.log_workflow_run(self._workflow_run_schema)
            # Separate metadata row
            meta = WorkflowMetadataSchema(
                workflow_run_id=self.workflow_run_id,
                description=description,
                version=version,
                tags=tags,
                labels=labels,
                config=self.config or None,
                created_at=now,
            )
            self.db_handler.log_workflow_metadata(meta)

    def _update_status(
        self,
        status: TaskStatus,
        completed_at: datetime | None = None,
    ) -> None:
        if self.db_handler is not None:
            self.db_handler.update_workflow_status(
                workflow_run_id=self.workflow_run_id,
                status=status,
                completed_at=completed_at,
            )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<{self.__class__.__name__} name={self.name!r}"
            f" tasks={len(self._tasks)} id={self.workflow_run_id!r}>"
        )
