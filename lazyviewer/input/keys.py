"""Keyboard dispatch facade for picker, tree-filter, and normal modes."""

from __future__ import annotations

from .key_normal import handle_normal_key
from .key_picker import handle_picker_key
from .key_registry import KeyComboBinding, KeyComboRegistry
from .key_tree_filter import handle_tree_filter_key

__all__ = [
    "KeyComboBinding",
    "KeyComboRegistry",
    "handle_picker_key",
    "handle_tree_filter_key",
    "handle_normal_key",
]
