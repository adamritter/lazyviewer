"""Public filter-panel controller exports.

This package owns the tree filter state machine used for both file filtering
and content-search result navigation in the left pane.
"""

from .controller import TreeFilterOps

__all__ = [
    "TreeFilterOps",
]
