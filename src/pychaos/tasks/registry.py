"""Task registry — maps task_type strings to concrete BaseTask subclasses.

Usage::

    from pychaos.tasks.registry import register_task, TaskRegistry

    @register_task
    class MyTask(BaseTask):
        task_type = "my_task"
        ...

    # Later
    cls = TaskRegistry.get("my_task")
    task = cls(name="run1", workflow_run_id="...", db_handler=db)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Type

if TYPE_CHECKING:
    from .base import BaseTask

logger = logging.getLogger(__name__)


class TaskRegistry:
    """Class-level registry mapping ``task_type`` → ``BaseTask`` subclass."""

    _registry: dict[str, Type["BaseTask"]] = {}

    @classmethod
    def register(cls, task_class: Type["BaseTask"]) -> Type["BaseTask"]:
        """Register a task class under its ``task_type`` attribute."""
        key = task_class.task_type
        if key == "base":
            raise ValueError(
                f"Cannot register {task_class.__name__}: task_type must be set "
                "to a unique string (not 'base')."
            )
        if key in cls._registry:
            logger.warning(
                "TaskRegistry: overwriting existing registration for task_type=%r "
                "(was %s, now %s)",
                key,
                cls._registry[key].__name__,
                task_class.__name__,
            )
        cls._registry[key] = task_class
        logger.debug("Registered task %r → %s", key, task_class.__name__)
        return task_class

    @classmethod
    def get(cls, task_type: str) -> Type["BaseTask"]:
        """Return the class for *task_type*, raising KeyError if unknown."""
        if task_type not in cls._registry:
            known = ", ".join(sorted(cls._registry))
            raise KeyError(
                f"Unknown task_type={task_type!r}. Registered types: [{known}]"
            )
        return cls._registry[task_type]

    @classmethod
    def list_types(cls) -> list[str]:
        """Return all registered task_type strings."""
        return sorted(cls._registry.keys())

    @classmethod
    def clear(cls) -> None:
        """Clear all registrations (useful in tests)."""
        cls._registry.clear()


def register_task(task_class: Type["BaseTask"]) -> Type["BaseTask"]:
    """Class decorator that registers *task_class* in ``TaskRegistry``."""
    return TaskRegistry.register(task_class)
