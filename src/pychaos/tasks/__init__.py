"""Tasks sub-package — base class and registry.

Import concrete task implementations here to ensure they are registered
when the package is first imported::

    # Example (add after creating tasks/my_task.py):
    from .my_task import MyTask  # noqa: F401 — triggers @register_task
"""

from .base import BaseTask
from .registry import TaskRegistry, register_task

__all__ = ["BaseTask", "TaskRegistry", "register_task"]
