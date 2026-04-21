"""BaseTask — abstract base for all PyChaos tasks.

Subclass this, implement the three abstract methods, and optionally call
``store_artifacts()`` inside ``process()`` or ``verify_output()``.

Example::

    from pychaos.tasks.base import BaseTask
    from pychaos.tasks.registry import register_task

    @register_task
    class SumTask(BaseTask):
        task_type = "sum"

        def init_context(self) -> None:
            numbers = self.config.get("numbers", [])
            if not isinstance(numbers, list):
                raise ValueError("config.numbers must be a list")
            self.context["numbers"] = numbers

        def process(self) -> None:
            total = sum(self.context["numbers"])
            self.output["total"] = total
            self.store_artifacts(
                name="sum_result",
                artifact_type="metric",
                value={"total": total},
            )

        def verify_output(self) -> None:
            if "total" not in self.output:
                raise AssertionError("Expected 'total' in output")
"""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ..db.models import TaskStatus
from ..db.schema import ArtifactSchema, TaskRunSchema

if TYPE_CHECKING:
    from ..db.handler import DatabaseHandler

logger = logging.getLogger(__name__)


class BaseTask(ABC):
    """Abstract base class for all workflow tasks.

    Parameters
    ----------
    name:
        Human-readable label for this task instance (not the type).
    workflow_run_id:
        ID of the parent WorkflowRun; used to link DB rows.
    db_handler:
        Optional.  When provided, the task logs its run and all artifacts to
        the database automatically.
    config:
        Arbitrary key-value configuration passed to the task at construction.
    """

    #: Must be overridden in every subclass before decorating with @register_task
    task_type: str = "base"

    def __init__(
        self,
        name: str,
        workflow_run_id: str,
        db_handler: "DatabaseHandler | None" = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.workflow_run_id = workflow_run_id
        self.db_handler = db_handler
        self.config: dict[str, Any] = config or {}
        self.task_run_id: str = str(uuid.uuid4())

        # Populated by init_context; consumed by process / verify_output
        self.context: dict[str, Any] = {}
        # Populated by process; inspected by verify_output
        self.output: dict[str, Any] = {}
        # Collected during the run for convenience (does not drive DB logging)
        self.artifacts: list[ArtifactSchema] = []

        self._task_run_schema: TaskRunSchema | None = None
        self._log_task_run_created()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(self) -> dict[str, Any]:
        """Orchestrate the task lifecycle.

        Calls ``init_context`` → ``process`` → ``verify_output`` in order.
        DB status is updated at each stage.  Any exception is re-raised after
        setting status to FAILED.

        Returns
        -------
        dict
            The contents of ``self.output`` after a successful run.
        """
        logger.info("Task [%s/%s] starting", self.task_type, self.name)
        self._update_status(TaskStatus.RUNNING)

        try:
            logger.debug("Task [%s] — init_context", self.name)
            self.init_context()

            logger.debug("Task [%s] — process", self.name)
            self.process()

            logger.debug("Task [%s] — verify_output", self.name)
            self.verify_output()

        except Exception as exc:
            logger.exception("Task [%s] failed: %s", self.name, exc)
            self._update_status(
                TaskStatus.FAILED,
                completed_at=datetime.now(timezone.utc),
                error_message=str(exc),
            )
            raise

        self._update_status(TaskStatus.COMPLETED, completed_at=datetime.now(timezone.utc))
        logger.info("Task [%s/%s] completed", self.task_type, self.name)
        return self.output

    # ------------------------------------------------------------------
    # Abstract sub-functions
    # ------------------------------------------------------------------

    @abstractmethod
    def init_context(self) -> None:
        """Initialise data required by the task and validate inputs.

        Populate ``self.context`` with everything ``process()`` will need.
        Raise ``ValueError`` (or any exception) to abort the task early with
        a meaningful error instead of an obscure crash later.
        """

    @abstractmethod
    def process(self) -> None:
        """Execute the core logic of the task.

        Read from ``self.context``, write results to ``self.output``.
        Call ``self.store_artifacts(...)`` for any notable outputs.
        """

    @abstractmethod
    def verify_output(self) -> None:
        """Assert that ``self.output`` meets expectations.

        Raise ``AssertionError`` (or any exception) when the output is
        missing required keys, out-of-range values, etc.
        """

    # ------------------------------------------------------------------
    # Artifact helper
    # ------------------------------------------------------------------

    def store_artifacts(
        self,
        name: str,
        artifact_type: str,
        value: dict[str, Any] | None = None,
        location: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactSchema:
        """Create and persist an artifact record.

        Parameters
        ----------
        name:
            Short identifier for the artifact (e.g. ``"predictions_csv"``).
        artifact_type:
            Category string (e.g. ``"file"``, ``"metric"``, ``"dataset"``).
        value:
            Inline JSON payload for small scalar outputs.
        location:
            URI / file path for larger outputs stored externally.
        metadata:
            Extra free-form key-value pairs.

        Returns
        -------
        ArtifactSchema
            The schema object that was (optionally) persisted.
        """
        artifact = ArtifactSchema(
            id=str(uuid.uuid4()),
            task_run_id=self.task_run_id,
            name=name,
            artifact_type=artifact_type,
            value=value,
            location=location,
            metadata=metadata,
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

    def _log_task_run_created(self) -> None:
        """Persist the initial PENDING task run row, if a handler is set."""
        self._task_run_schema = TaskRunSchema(
            id=self.task_run_id,
            workflow_run_id=self.workflow_run_id,
            task_name=self.name,
            task_type=self.task_type,
            status=TaskStatus.PENDING,
            started_at=datetime.now(timezone.utc),
        )
        if self.db_handler is not None:
            self.db_handler.log_task_run(self._task_run_schema)

    def _update_status(
        self,
        status: TaskStatus,
        completed_at: datetime | None = None,
        error_message: str | None = None,
    ) -> None:
        if self.db_handler is not None:
            self.db_handler.update_task_status(
                task_run_id=self.task_run_id,
                status=status,
                completed_at=completed_at,
                error_message=error_message,
            )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<{self.__class__.__name__} name={self.name!r}"
            f" type={self.task_type!r} id={self.task_run_id!r}>"
        )
