"""Public runtime orchestration entry points.

This package groups the interactive pager bootstrap (`run_pager`) and the
lower-level event loop contracts used by tests and composition code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .loop import RuntimeLoopCallbacks, RuntimeLoopTiming


def run_pager(*args, **kwargs):
    """Lazily import pager entrypoint to avoid heavy runtime bootstrap on import."""
    from .app import run_pager as _run_pager

    return _run_pager(*args, **kwargs)


def run_main_loop(*args, **kwargs):
    """Lazily import loop runner to avoid package-import cycles."""
    from .loop import run_main_loop as _run_main_loop

    return _run_main_loop(*args, **kwargs)


def __getattr__(name: str):
    if name in {"RuntimeLoopCallbacks", "RuntimeLoopTiming"}:
        from . import loop as _loop

        return getattr(_loop, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "run_pager",
    "RuntimeLoopCallbacks",
    "RuntimeLoopTiming",
    "run_main_loop",
]
