"""Public picker-panel controller exports.

The picker panel unifies symbol outline jumping and command palette actions
behind one controller plus display/source line mapping helpers.
"""

from .controller import NavigationPickerOps
from .line_map import (
    first_display_index_for_source_line as _first_display_index_for_source_line,
)
from .line_map import source_line_for_display_index as _source_line_for_display_index

__all__ = [
    "NavigationPickerOps",
    "_source_line_for_display_index",
    "_first_display_index_for_source_line",
]
