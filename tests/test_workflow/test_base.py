"""Tests for BaseWorkflow."""

import pytest

from pychaos.workflow.base import BaseWorkflow
from pychaos.tasks.base import BaseTask
from pychaos.db.models import TaskStatus
from pychaos.db.schema import WorkflowRunSchema


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Minimal task stubs
# ---------------------------------------------------------------------------

class AddTask(BaseTask):
    task_type = "test_add"

    def init_context(self) -> None:
        self.context["a"] = self.config.get("a", 0)
        self.context["b"] = self.config.get("b", 0)

    def process(self) -> None:
        self.output["sum"] = self.context["a"] + self.context["b"]

    def verify_output(self) -> None:
        assert "sum" in self.output


class BoomTask(BaseTask):
    task_type = "test_boom"

    def init_context(self) -> None:
        pass

    def process(self) -> None:
        raise RuntimeError("kaboom")

    def verify_output(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Workflow tests
# ---------------------------------------------------------------------------

class TestBaseWorkflow:
    def test_run_single_task(self, workflow_run_id):
        wf = BaseWorkflow(name="add_wf")
        task = AddTask("add1", wf.workflow_run_id, config={"a": 3, "b": 4})
        wf.add_task(task)
        results = wf.run()
        assert results["add1"] == {"sum": 7}

    def test_run_multiple_tasks(self, workflow_run_id):
        wf = BaseWorkflow(name="multi_wf")
        wf.add_tasks(
            AddTask("add1", wf.workflow_run_id, config={"a": 1, "b": 2}),
            AddTask("add2", wf.workflow_run_id, config={"a": 10, "b": 20}),
        )
        results = wf.run()
        assert results["add1"]["sum"] == 3
        assert results["add2"]["sum"] == 30

    def test_chained_add_task(self):
        wf = BaseWorkflow(name="chain_wf")
        returned = wf.add_task(AddTask("t", wf.workflow_run_id))
        assert returned is wf  # fluent API

    def test_failing_task_propagates(self):
        wf = BaseWorkflow(name="fail_wf")
        wf.add_task(BoomTask("boom", wf.workflow_run_id))
        with pytest.raises(RuntimeError, match="kaboom"):
            wf.run()

    def test_empty_workflow_completes(self):
        wf = BaseWorkflow(name="empty_wf")
        results = wf.run()
        assert results == {}

    def test_workflow_with_db_handler(self, db_handler):
        wf = BaseWorkflow(name="db_wf", db_handler=db_handler)
        task = AddTask("t1", wf.workflow_run_id, db_handler=db_handler, config={"a": 5, "b": 5})
        wf.add_task(task)
        wf.run()

        fetched = db_handler.get_workflow_run(wf.workflow_run_id)
        assert fetched is not None
        assert fetched.status == TaskStatus.COMPLETED

        task_runs = db_handler.get_task_runs(wf.workflow_run_id)
        assert len(task_runs) == 1
        assert task_runs[0].status == TaskStatus.COMPLETED

    def test_failing_workflow_marks_failed_in_db(self, db_handler):
        wf = BaseWorkflow(name="fail_db_wf", db_handler=db_handler)
        wf.add_task(BoomTask("boom", wf.workflow_run_id, db_handler=db_handler))
        with pytest.raises(RuntimeError):
            wf.run()

        fetched = db_handler.get_workflow_run(wf.workflow_run_id)
        assert fetched.status == TaskStatus.FAILED

    def test_build_hook_called_when_no_tasks(self):
        """BaseWorkflow.build() is called automatically if task list is empty."""

        class AutoBuildWorkflow(BaseWorkflow):
            def build(self) -> None:
                self.add_task(AddTask("auto", self.workflow_run_id, config={"a": 1, "b": 1}))

        wf = AutoBuildWorkflow(name="auto_wf")
        results = wf.run()
        assert results["auto"]["sum"] == 2

    def test_task_output_accessor(self):
        wf = BaseWorkflow(name="accessor_wf")
        wf.add_task(AddTask("t", wf.workflow_run_id, config={"a": 2, "b": 3}))
        wf.run()
        assert wf.task_output("t") == {"sum": 5}
        assert wf.task_output("nonexistent") is None
