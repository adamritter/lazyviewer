"""Public package surface for lazyviewer.

Exports ``main`` for programmatic CLI invocation.
Most implementation lives in submodules under ``lazyviewer``.
"""

from __future__ import annotations


def main(*args, **kwargs):
    """Lazily import CLI entrypoint to keep package imports lightweight."""
    import gc

    # Importing the UI creates many temporary class/annotation objects. Let
    # reference counting reclaim them, and restore cyclic GC before running.
    enabled = gc.isenabled()
    gc.disable()
    try:
        from .cli import main as _main
    finally:
        if enabled:
            gc.enable()

    return _main(*args, **kwargs)

__all__ = ["main"]
