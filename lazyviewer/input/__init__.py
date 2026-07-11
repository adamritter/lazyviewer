"""Input-layer public API for key decoding and interaction handlers.

Exports are intentionally split between low-level terminal decoding (`read_key`)
and higher-level mode handlers used by the runtime loop.
"""

from .reader import (
    ESC_SEQUENCE_TIMEOUT_MS,
    _PENDING_BYTES,
    _PENDING_KEYS,
    coalesce_mouse_wheel_events,
    has_pending_input,
    read_key,
)
from .commands import AdjustPaneWidth, DispatchKey, IgnoreInput, InputCommand, map_input
from .key_normal import NormalKeyContext, NormalKeyHandler, handle_normal_key
from .key_registry import KeyComboBinding, KeyComboRegistry

__all__ = [
    "read_key",
    "_PENDING_BYTES",
    "_PENDING_KEYS",
    "ESC_SEQUENCE_TIMEOUT_MS",
    "coalesce_mouse_wheel_events",
    "has_pending_input",
    "KeyComboBinding",
    "KeyComboRegistry",
    "NormalKeyContext",
    "NormalKeyHandler",
    "handle_normal_key",
    "AdjustPaneWidth",
    "DispatchKey",
    "IgnoreInput",
    "InputCommand",
    "map_input",
]
