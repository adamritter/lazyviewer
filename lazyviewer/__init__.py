"""Public package surface for lazyviewer.

Exports ``main`` for programmatic CLI invocation.
Most implementation lives in submodules under ``lazyviewer``.
"""

from __future__ import annotations


def main(*args, **kwargs):
    """Lazily import CLI entrypoint to keep package imports lightweight."""
    from .cli import main as _main

    return _main(*args, **kwargs)

__all__ = ["main"]
