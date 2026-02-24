"""Public runtime orchestration entry points.

This package groups the interactive pager bootstrap (`run_pager`) and the
lower-level event loop contracts used by tests and composition code.
"""

from .app import run_pager
from .loop import RuntimeLoopCallbacks, RuntimeLoopTiming, run_main_loop

__all__ = [
    "run_pager",
    "RuntimeLoopCallbacks",
    "RuntimeLoopTiming",
    "run_main_loop",
]
