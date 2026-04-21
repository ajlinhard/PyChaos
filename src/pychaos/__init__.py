"""PyChaos — modular workflow execution engine."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pychaos")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0-dev"

__all__ = ["__version__"]
