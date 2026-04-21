"""Tests for BaseTask and TaskRegistry."""

import pytest

from pychaos.tasks.base import BaseTask
from pychaos.tasks.registry import TaskRegistry, register_task
from pychaos.db.models import TaskStatus


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Concrete task helpers
# ---------------------------------------------------------------------------

class SuccessTask(BaseTask):
    task_type = "test_success"

    def init_context(self) -> None:
        self.context["value"] = self.config.get("value", 10)

    def process(self) -> None:
        self.output["result"] = self.context["value"] * 2
        self.store_artifacts(
            name="doubled",
            artifact_type="metric",
            value={"result": self.output["result"]},
        )

    def verify_output(self) -> None:
        assert "result" in self.output


class FailingInitTask(BaseTask):
    task_type = "test_fail_init"

    def init_context(self) -> None:
        raise ValueError("bad config")

    def process(self) -> None:
        pass

    def verify_output(self) -> None:
        pass


class BadVerifyTask(BaseTask):
    task_type = "test_fail_verify"

    def init_context(self) -> None:
        pass

    def process(self) -> None:
        pass  # Deliberately does NOT set output

    def verify_output(self) -> None:
        assert "result" in self.output, "Missing 'result'"


# ---------------------------------------------------------------------------
# BaseTask execution tests
# ---------------------------------------------------------------------------

class TestBaseTaskExecution:
    def test_successful_run_without_db(self, workflow_run_id):
        task = SuccessTask(
            name="t1",
            workflow_run_id=workflow_run_id,
            config={"value": 5},
        )
        output = task.execute()
        assert output == {"result": 10}

    def test_artifact_stored_in_memory(self, workflow_run_id):
        task = SuccessTask(name="t1", workflow_run_id=workflow_run_id, config={"value": 3})
        task.execute()
        assert len(task.artifacts) == 1
        assert task.artifacts[0].name == "doubled"
        assert task.artifacts[0].value == {"result": 6}

    def test_failed_init_raises(self, workflow_run_id):
        task = FailingInitTask(name="t2", workflow_run_id=workflow_run_id)
        with pytest.raises(ValueError, match="bad config"):
            task.execute()

    def test_failed_verify_raises(self, workflow_run_id):
        task = BadVerifyTask(name="t3", workflow_run_id=workflow_run_id)
        with pytest.raises(AssertionError, match="Missing 'result'"):
            task.execute()

    def test_successful_run_with_db(self, db_handler, workflow_run_id):
        from pychaos.db.schema import WorkflowRunSchema
        wf = WorkflowRunSchema(id=workflow_run_id, workflow_name="wf")
        db_handler.log_workflow_run(wf)

        task = SuccessTask(
            name="db_task",
            workflow_run_id=workflow_run_id,
            db_handler=db_handler,
            config={"value": 7},
        )
        task.execute()

        runs = db_handler.get_task_runs(workflow_run_id)
        assert len(runs) == 1
        assert runs[0].status == TaskStatus.COMPLETED

        artifacts = db_handler.get_artifacts(task.task_run_id)
        assert len(artifacts) == 1
        assert artifacts[0].value == {"result": 14}

    def test_failed_task_logged_to_db(self, db_handler, workflow_run_id):
        from pychaos.db.schema import WorkflowRunSchema
        wf = WorkflowRunSchema(id=workflow_run_id, workflow_name="wf")
        db_handler.log_workflow_run(wf)

        task = FailingInitTask(
            name="fail_db",
            workflow_run_id=workflow_run_id,
            db_handler=db_handler,
        )
        with pytest.raises(ValueError):
            task.execute()

        runs = db_handler.get_task_runs(workflow_run_id)
        assert runs[0].status == TaskStatus.FAILED


# ---------------------------------------------------------------------------
# TaskRegistry tests
# ---------------------------------------------------------------------------

class TestTaskRegistry:
    def setup_method(self):
        # Preserve existing registry; only clean up what this test adds
        self._originals = dict(TaskRegistry._registry)

    def teardown_method(self):
        TaskRegistry._registry.clear()
        TaskRegistry._registry.update(self._originals)

    def test_register_and_get(self):
        @register_task
        class MyRegisteredTask(BaseTask):
            task_type = "my_registered"

            def init_context(self): pass
            def process(self): pass
            def verify_output(self): pass

        cls = TaskRegistry.get("my_registered")
        assert cls is MyRegisteredTask

    def test_get_unknown_raises(self):
        with pytest.raises(KeyError, match="unknown_xyz"):
            TaskRegistry.get("unknown_xyz")

    def test_list_types(self):
        types_before = set(TaskRegistry.list_types())
        @register_task
        class ListTask(BaseTask):
            task_type = "list_task_test"
            def init_context(self): pass
            def process(self): pass
            def verify_output(self): pass

        assert "list_task_test" in TaskRegistry.list_types()

    def test_register_base_type_raises(self):
        with pytest.raises(ValueError, match="task_type must be set"):
            class BadTask(BaseTask):
                task_type = "base"
                def init_context(self): pass
                def process(self): pass
                def verify_output(self): pass
            register_task(BadTask)
