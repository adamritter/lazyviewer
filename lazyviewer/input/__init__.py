"""Input-layer public API for key decoding and interaction handlers.

Exports are intentionally split between low-level terminal decoding (`read_key`)
and higher-level mode handlers used by the runtime loop.
"""

from .reader import ESC_SEQUENCE_TIMEOUT_MS, _PENDING_BYTES, read_key
from .commands import AdjustPaneWidth, DispatchKey, IgnoreInput, InputCommand, map_input
from .key_normal import NormalKeyContext, NormalKeyHandler, handle_normal_key
from .key_registry import KeyComboBinding, KeyComboRegistry

__all__ = [
    "read_key",
    "_PENDING_BYTES",
    "ESC_SEQUENCE_TIMEOUT_MS",
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
