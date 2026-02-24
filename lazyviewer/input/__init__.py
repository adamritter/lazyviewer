"""Input-layer public API for key decoding and interaction handlers.

Exports are intentionally split between low-level terminal decoding (`read_key`)
and higher-level mode handlers used by the runtime loop.
"""

from .reader import ESC_SEQUENCE_TIMEOUT_MS, _PENDING_BYTES, read_key
from .keys import (
    KeyComboBinding,
    KeyComboRegistry,
    NormalKeyActions,
    PickerKeyCallbacks,
    TreeFilterKeyCallbacks,
    handle_normal_key,
    handle_picker_key,
    handle_tree_filter_key,
)
from .mouse import TreeMouseCallbacks, TreeMouseHandlers, _handle_tree_mouse_wheel

__all__ = [
    "read_key",
    "_PENDING_BYTES",
    "ESC_SEQUENCE_TIMEOUT_MS",
    "KeyComboBinding",
    "KeyComboRegistry",
    "PickerKeyCallbacks",
    "TreeFilterKeyCallbacks",
    "NormalKeyActions",
    "handle_picker_key",
    "handle_tree_filter_key",
    "handle_normal_key",
    "TreeMouseCallbacks",
    "TreeMouseHandlers",
    "_handle_tree_mouse_wheel",
]
