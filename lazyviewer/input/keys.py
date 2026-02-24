"""Keyboard dispatch facade for picker, tree-filter, and normal modes."""

from __future__ import annotations

from .key_normal import NormalKeyActions, handle_normal_key
from .key_picker import PickerKeyCallbacks, handle_picker_key
from .key_registry import KeyComboBinding, KeyComboRegistry
from .key_tree_filter import TreeFilterKeyCallbacks, handle_tree_filter_key

__all__ = [
    "KeyComboBinding",
    "KeyComboRegistry",
    "PickerKeyCallbacks",
    "TreeFilterKeyCallbacks",
    "NormalKeyActions",
    "handle_picker_key",
    "handle_tree_filter_key",
    "handle_normal_key",
]
