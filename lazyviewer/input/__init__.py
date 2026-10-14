"""Input-layer public API for key decoding and interaction handlers.

Exports are intentionally split between low-level terminal decoding (`read_key`)
and higher-level mode handlers used by the runtime loop.
"""

from .reader import ESC_SEQUENCE_TIMEOUT_MS, _PENDING_BYTES, read_key
from .keys import (
    KeyComboBinding,
    KeyComboRegistry,
    NormalKeyHandler,
    handle_normal_key,
    handle_picker_key,
    handle_tree_filter_key,
)

__all__ = [
    "read_key",
    "_PENDING_BYTES",
    "ESC_SEQUENCE_TIMEOUT_MS",
    "KeyComboBinding",
    "KeyComboRegistry",
    "NormalKeyContext",
    "NormalKeyHandler",
    "handle_picker_key",
    "handle_tree_filter_key",
    "handle_normal_key",
]


def __getattr__(name):
    if name == "NormalKeyContext":
        from .key_context import NormalKeyContext
        return NormalKeyContext
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
