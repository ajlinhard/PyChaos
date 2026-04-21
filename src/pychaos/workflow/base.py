"""BaseWorkflow — orchestrates an ordered sequence of tasks.

Usage::

    from pychaos.workflow.base import BaseWorkflow
    from pychaos.tasks.base import BaseTask

    class MyWorkflow(BaseWorkflow):
        name = "etl_pipeline"

        def build(self) -> None:
            self.add_task(ExtractTask("extract", self.workflow_run_id, self.db_handler))
            self.add_task(TransformTask("transform", self.workflow_run_id, self.db_handler))
            self.add_task(LoadTask("load", self.workflow_run_id, self.db_handler))

    wf = MyWorkflow(db_handler=db)
    results = wf.run()
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ..db.models import TaskStatus
from ..db.schema import WorkflowRunSchema

if TYPE_CHECKING:
    from ..db.handler import DatabaseHandler
    from ..tasks.base import BaseTask

logger = logging.getLogger(__name__)


class BaseWorkflow:
    """Sequential workflow executor.

    Parameters
    ----------
    name:
        Logical name for this workflow (stored in the DB).
    db_handler:
        Optional.  When provided, workflow and task runs are persisted.
    config:
        Arbitrary configuration dict forwarded to tasks as needed.
    """

    def __init__(
        self,
        name: str,
        db_handler: "DatabaseHandler | None" = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.db_handler = db_handler
        self.config: dict[str, Any] = config or {}
        self.workflow_run_id: str = str(uuid.uuid4())

        self._tasks: list["BaseTask"] = []
        self._results: dict[str, Any] = {}
        self._workflow_run_schema: WorkflowRunSchema | None = None

        self._log_workflow_created()

    # ------------------------------------------------------------------
    # Task management
    # ------------------------------------------------------------------

    def add_task(self, task: "BaseTask") -> "BaseWorkflow":
        """Append a task to the execution queue.  Returns self for chaining."""
        self._tasks.append(task)
        return self

    def add_tasks(self, *tasks: "BaseTask") -> "BaseWorkflow":
        """Append multiple tasks at once."""
        for task in tasks:
            self.add_task(task)
        return self

    @property
    def tasks(self) -> list["BaseTask"]:
        """Read-only view of the current task list."""
        return list(self._tasks)

    # ------------------------------------------------------------------
    # Build hook
    # ------------------------------------------------------------------

    def build(self) -> None:
        """Override in subclasses to construct the task list declaratively.

        Called automatically at the start of :meth:`run` if the task list is
        empty.  Leave the default empty implementation if you prefer to build
        the workflow imperatively before calling ``run()``.
        """

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        """Execute all tasks in order.

        Tasks are run sequentially.  If any task raises an exception the
        workflow is marked FAILED and the exception propagates.

        Returns
        -------
        dict
            Mapping of ``task.name → task.output`` for every completed task.
        """
        if not self._tasks:
            self.build()

        if not self._tasks:
            logger.warning("Workflow [%s] has no tasks; nothing to run.", self.name)
            self._update_status(TaskStatus.COMPLETED, completed_at=datetime.now(timezone.utc))
            return {}

        logger.info(
            "Workflow [%s] starting — %d task(s)", self.name, len(self._tasks)
        )
        self._update_status(TaskStatus.RUNNING)

        try:
            for task in self._tasks:
                self._results[task.name] = task.execute()
        except Exception as exc:
            logger.error("Workflow [%s] failed: %s", self.name, exc)
            self._update_status(
                TaskStatus.FAILED, completed_at=datetime.now(timezone.utc)
            )
            raise

        self._update_status(TaskStatus.COMPLETED, completed_at=datetime.now(timezone.utc))
        logger.info("Workflow [%s] completed successfully.", self.name)
        return dict(self._results)

    # ------------------------------------------------------------------
    # Result access
    # ------------------------------------------------------------------

    @property
    def results(self) -> dict[str, Any]:
        """Task outputs accumulated so far (populated during :meth:`run`)."""
        return dict(self._results)

    def task_output(self, task_name: str) -> Any:
        """Return the output dict for a named task, or None if not run yet."""
        return self._results.get(task_name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _log_workflow_created(self) -> None:
        self._workflow_run_schema = WorkflowRunSchema(
            id=self.workflow_run_id,
            workflow_name=self.name,
            status=TaskStatus.PENDING,
            started_at=datetime.now(timezone.utc),
            metadata=self.config or None,
        )
        if self.db_handler is not None:
            self.db_handler.log_workflow_run(self._workflow_run_schema)

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
