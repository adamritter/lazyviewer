"""Public picker-panel controller exports.

The picker panel unifies symbol outline jumping and command palette actions
behind one controller plus display/source line mapping helpers.
"""

from .controller import (
    NavigationPickerDeps,
    NavigationPickerOps,
    _first_display_index_for_source_line,
    _source_line_for_display_index,
)

__all__ = [
    "NavigationPickerDeps",
    "NavigationPickerOps",
    "_source_line_for_display_index",
    "_first_display_index_for_source_line",
]
