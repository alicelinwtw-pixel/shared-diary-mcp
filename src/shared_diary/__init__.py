"""Shared Diary MCP core package."""

from .store import DiaryStore, DiaryError, NotFound, PermissionDenied
from .tools import DiaryTools

__version__ = "0.1.0"

__all__ = [
    "DiaryStore",
    "DiaryTools",
    "DiaryError",
    "NotFound",
    "PermissionDenied",
    "__version__",
]
