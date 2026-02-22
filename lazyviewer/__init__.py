"""Public package surface for lazyviewer.

Exports ``main`` for programmatic CLI invocation.
Most implementation lives in submodules under ``lazyviewer``.
"""

from .cli import main

__all__ = ["main"]
