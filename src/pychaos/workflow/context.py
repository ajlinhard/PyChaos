"""WorkflowContext — shared state object passed to every task in a workflow run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..db.schema import AIModelSchema, ArtifactSchema, PromptSchema


@dataclass
class WorkflowContext:
    """Carries cross-task state for a single workflow execution.

    The workflow creates one instance before calling ``run()`` and passes it
    to every task via ``task.execute(context=...)``.  Tasks may read from
    ``task_outputs`` / ``pre_artifacts`` and write to ``shared_state``.

    Parameters
    ----------
    workflow_run_id:
        The ID of the parent WorkflowRun row.
    a2a_context_id:
        A2A context identifier propagated from the calling agent.
    a2a_session_id:
        A2A session identifier for stateful multi-turn interactions.
    """

    workflow_run_id: str

    # A2A correlation identifiers
    a2a_context_id: str | None = None
    a2a_session_id: str | None = None

    # Outputs from previously completed tasks, keyed by task.name
    task_outputs: dict[str, Any] = field(default_factory=dict)

    # Free-form state shared and mutated between tasks
    shared_state: dict[str, Any] = field(default_factory=dict)

    # Artifacts registered at the workflow level before any task runs
    pre_artifacts: list["ArtifactSchema"] = field(default_factory=list)

    # AI infrastructure registered for use in this workflow, keyed by name
    models: dict[str, "AIModelSchema"] = field(default_factory=dict)
    prompts: dict[str, "PromptSchema"] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Helpers for tasks
    # ------------------------------------------------------------------

    def record_task_output(self, task_name: str, output: dict[str, Any]) -> None:
        """Called by the workflow after each task completes."""
        self.task_outputs[task_name] = output

    def get_task_output(self, task_name: str) -> dict[str, Any] | None:
        """Return a previous task's output dict, or None if not yet run."""
        return self.task_outputs.get(task_name)

    def get_model(self, name: str) -> "AIModelSchema | None":
        """Return a registered AIModelSchema by its friendly name."""
        return self.models.get(name)

    def get_prompt(self, name: str) -> "PromptSchema | None":
        """Return a registered PromptSchema by name."""
        return self.prompts.get(name)
