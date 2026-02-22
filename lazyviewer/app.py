"""Compatibility wrapper for the runtime pager entrypoint.

Historically callers import ``run_pager`` from ``lazyviewer.app``.
The real implementation now lives in ``lazyviewer.app_runtime``.
"""

from __future__ import annotations

from .app_runtime import run_pager

__all__ = ["run_pager"]
