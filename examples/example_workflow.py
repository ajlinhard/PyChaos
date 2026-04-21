"""End-to-end example — runs without a real database (db_handler=None).

Run with:
    python examples/example_workflow.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Allow running from the repo root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pychaos.tasks.base import BaseTask
from pychaos.tasks.registry import register_task
from pychaos.workflow.base import BaseWorkflow

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")


# ---------------------------------------------------------------------------
# Task definitions
# ---------------------------------------------------------------------------

@register_task
class LoadDataTask(BaseTask):
    """Simulates loading a dataset."""

    task_type = "load_data"

    def init_context(self) -> None:
        source = self.config.get("source", "demo")
        if not source:
            raise ValueError("config.source is required")
        self.context["source"] = source

    def process(self) -> None:
        # In a real task this would read from disk / S3 / DB
        records = [{"id": i, "value": i * 10} for i in range(1, 6)]
        self.output["records"] = records
        self.store_artifacts(
            name="raw_records",
            artifact_type="dataset",
            value={"count": len(records), "source": self.context["source"]},
        )

    def verify_output(self) -> None:
        assert "records" in self.output, "records missing from output"
        assert len(self.output["records"]) > 0, "no records loaded"


@register_task
class TransformDataTask(BaseTask):
    """Doubles every value in the dataset."""

    task_type = "transform_data"

    def init_context(self) -> None:
        # Pull records from the workflow's shared context if provided
        raw = self.config.get("records")
        if raw is None:
            raise ValueError("config.records is required")
        self.context["records"] = raw

    def process(self) -> None:
        transformed = [
            {**r, "value": r["value"] * 2} for r in self.context["records"]
        ]
        self.output["transformed"] = transformed
        self.store_artifacts(
            name="transformed_records",
            artifact_type="dataset",
            value={"count": len(transformed)},
        )

    def verify_output(self) -> None:
        assert "transformed" in self.output
        assert all(r["value"] % 2 == 0 for r in self.output["transformed"])


@register_task
class SummariseTask(BaseTask):
    """Computes summary statistics."""

    task_type = "summarise"

    def init_context(self) -> None:
        records = self.config.get("records", [])
        self.context["values"] = [r["value"] for r in records]

    def process(self) -> None:
        values = self.context["values"]
        self.output["total"] = sum(values)
        self.output["mean"] = sum(values) / len(values) if values else 0
        self.store_artifacts(
            name="summary_stats",
            artifact_type="metric",
            value={"total": self.output["total"], "mean": self.output["mean"]},
        )

    def verify_output(self) -> None:
        assert "total" in self.output
        assert "mean" in self.output


# ---------------------------------------------------------------------------
# Workflow definition
# ---------------------------------------------------------------------------

class ETLWorkflow(BaseWorkflow):
    """Extract → Transform → Summarise pipeline."""

    def build(self) -> None:
        # Step 1: load
        load = LoadDataTask(
            name="load",
            workflow_run_id=self.workflow_run_id,
            db_handler=self.db_handler,
            config={"source": "demo_source"},
        )
        self.add_task(load)

        # Step 2: transform — fed by load output after run() begins
        # (for real pipelines wire via a shared context dict or pipeline bus)
        transform = TransformDataTask(
            name="transform",
            workflow_run_id=self.workflow_run_id,
            db_handler=self.db_handler,
            config={"records": []},  # will be patched at runtime in on_task_done hook
        )
        self.add_task(transform)

        summarise = SummariseTask(
            name="summarise",
            workflow_run_id=self.workflow_run_id,
            db_handler=self.db_handler,
            config={"records": []},
        )
        self.add_task(summarise)

    def run(self):
        """Override to wire task outputs into downstream task configs."""
        if not self._tasks:
            self.build()

        from pychaos.db.models import TaskStatus
        from datetime import datetime, timezone

        self._update_status(TaskStatus.RUNNING)
        try:
            for i, task in enumerate(self._tasks):
                result = task.execute()
                self._results[task.name] = result

                # Wire load → transform
                if task.name == "load" and i + 1 < len(self._tasks):
                    next_task = self._tasks[i + 1]
                    next_task.config["records"] = result.get("records", [])

                # Wire transform → summarise
                if task.name == "transform" and i + 1 < len(self._tasks):
                    next_task = self._tasks[i + 1]
                    next_task.config["records"] = result.get("transformed", [])

        except Exception:
            self._update_status(TaskStatus.FAILED, completed_at=datetime.now(timezone.utc))
            raise

        self._update_status(TaskStatus.COMPLETED, completed_at=datetime.now(timezone.utc))
        return dict(self._results)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    wf = ETLWorkflow(name="demo_etl")
    results = wf.run()

    print("\n=== Workflow Results ===")
    for task_name, output in results.items():
        print(f"  {task_name}: {output}")

    print("\n=== Registered task types ===")
    from pychaos.tasks.registry import TaskRegistry
    print(" ", ", ".join(TaskRegistry.list_types()))
