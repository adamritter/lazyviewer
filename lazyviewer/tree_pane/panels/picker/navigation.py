"""Compatibility module for picker navigation patch points."""

from ....runtime.config import save_named_marks
from ....runtime.navigation import JumpLocation, is_named_mark_key

__all__ = [
    "JumpLocation",
    "is_named_mark_key",
    "save_named_marks",
]
