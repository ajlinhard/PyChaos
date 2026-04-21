"""Tests for SQLAlchemy models and Pydantic schemas.

Marked as `unit` — uses the SQLite db_handler fixture; no Postgres needed.
"""

import pytest
from datetime import datetime, timezone

from pychaos.db.models import TaskStatus, WorkflowRunModel, TaskRunModel, ArtifactModel
from pychaos.db.schema import (
    WorkflowRunSchema,
    TaskRunSchema,
    ArtifactSchema,
    A2AMessage,
    A2ATaskState,
    TextPart,
    DataPart,
    FilePart,
)


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# ORM smoke tests
# ---------------------------------------------------------------------------

class TestWorkflowRunModel:
    def test_insert_and_fetch(self, db_handler):
        schema = WorkflowRunSchema(workflow_name="test_wf")
        db_handler.log_workflow_run(schema)

        fetched = db_handler.get_workflow_run(schema.id)
        assert fetched is not None
        assert fetched.workflow_name == "test_wf"
        assert fetched.status == TaskStatus.PENDING

    def test_update_status(self, db_handler):
        schema = WorkflowRunSchema(workflow_name="update_wf")
        db_handler.log_workflow_run(schema)
        db_handler.update_workflow_status(schema.id, TaskStatus.COMPLETED)

        fetched = db_handler.get_workflow_run(schema.id)
        assert fetched.status == TaskStatus.COMPLETED


class TestTaskRunModel:
    def test_insert_and_fetch(self, db_handler, workflow_run_id):
        wf_schema = WorkflowRunSchema(id=workflow_run_id, workflow_name="parent_wf")
        db_handler.log_workflow_run(wf_schema)

        task_schema = TaskRunSchema(
            workflow_run_id=workflow_run_id,
            task_name="sum_task",
            task_type="sum",
        )
        db_handler.log_task_run(task_schema)

        runs = db_handler.get_task_runs(workflow_run_id)
        assert len(runs) == 1
        assert runs[0].task_name == "sum_task"
        assert runs[0].status == TaskStatus.PENDING

    def test_update_status_with_error(self, db_handler, workflow_run_id):
        wf_schema = WorkflowRunSchema(id=workflow_run_id, workflow_name="err_wf")
        db_handler.log_workflow_run(wf_schema)

        task_schema = TaskRunSchema(
            workflow_run_id=workflow_run_id,
            task_name="fail_task",
            task_type="failing",
        )
        db_handler.log_task_run(task_schema)
        db_handler.update_task_status(
            task_schema.id,
            TaskStatus.FAILED,
            error_message="division by zero",
        )

        runs = db_handler.get_task_runs(workflow_run_id)
        assert runs[0].status == TaskStatus.FAILED


class TestArtifactModel:
    def test_insert_and_fetch(self, db_handler, workflow_run_id):
        wf = WorkflowRunSchema(id=workflow_run_id, workflow_name="art_wf")
        db_handler.log_workflow_run(wf)

        task = TaskRunSchema(workflow_run_id=workflow_run_id, task_name="t", task_type="t")
        db_handler.log_task_run(task)

        art = ArtifactSchema(
            task_run_id=task.id,
            name="output_csv",
            artifact_type="file",
            location="/tmp/output.csv",
            value={"rows": 42},
        )
        db_handler.log_artifact(art)

        artifacts = db_handler.get_artifacts(task.id)
        assert len(artifacts) == 1
        assert artifacts[0].name == "output_csv"
        assert artifacts[0].location == "/tmp/output.csv"


# ---------------------------------------------------------------------------
# Pydantic schema tests
# ---------------------------------------------------------------------------

class TestA2ASchemas:
    def test_text_part(self):
        msg = A2AMessage.text(role="agent", text="hello")
        assert len(msg.parts) == 1
        assert isinstance(msg.parts[0], TextPart)
        assert msg.parts[0].text == "hello"

    def test_data_part(self):
        msg = A2AMessage.data(role="user", payload={"key": "value"})
        assert isinstance(msg.parts[0], DataPart)
        assert msg.parts[0].data == {"key": "value"}

    def test_task_status_state_mapping(self):
        assert A2ATaskState.from_task_status(TaskStatus.PENDING) == A2ATaskState.SUBMITTED
        assert A2ATaskState.from_task_status(TaskStatus.RUNNING) == A2ATaskState.WORKING
        assert A2ATaskState.from_task_status(TaskStatus.COMPLETED) == A2ATaskState.COMPLETED
        assert A2ATaskState.from_task_status(TaskStatus.FAILED) == A2ATaskState.FAILED

    def test_task_run_to_a2a(self, db_handler, workflow_run_id):
        wf = WorkflowRunSchema(id=workflow_run_id, workflow_name="a2a_wf")
        db_handler.log_workflow_run(wf)

        task_schema = TaskRunSchema(
            workflow_run_id=workflow_run_id,
            task_name="a2a_task",
            task_type="test",
            status=TaskStatus.COMPLETED,
        )
        a2a_task = task_schema.to_a2a_task()
        assert a2a_task.status.state == A2ATaskState.COMPLETED
        assert a2a_task.id == task_schema.id

    def test_artifact_to_a2a(self):
        art = ArtifactSchema(
            task_run_id="tid",
            name="report",
            artifact_type="file",
            location="s3://bucket/report.csv",
            value={"rows": 100},
        )
        a2a_art = art.to_a2a()
        assert a2a_art.name == "report"
        # Both data and file parts should be present
        part_types = {type(p).__name__ for p in a2a_art.parts}
        assert "DataPart" in part_types
        assert "FilePart" in part_types


class TestWorkflowRunSchemaFromAttributes:
    def test_round_trip(self, db_handler):
        """Pydantic schema must deserialise from a SQLAlchemy ORM model."""
        schema = WorkflowRunSchema(workflow_name="rt_wf")
        db_handler.log_workflow_run(schema)

        with db_handler.get_session() as session:
            orm_obj = session.get(WorkflowRunModel, schema.id)
            assert orm_obj is not None
            rebuilt = WorkflowRunSchema.model_validate(orm_obj)

        assert rebuilt.id == schema.id
        assert rebuilt.workflow_name == "rt_wf"
