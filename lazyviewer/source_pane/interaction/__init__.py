"""Source-pane interaction modules."""

from .events import directory_preview_target_for_display_line, handle_preview_click
from .mouse import SourcePaneClickResult, SourcePaneMouseHandlers
from .geometry import SourcePaneGeometry, copy_selected_source_range

__all__ = [
    "directory_preview_target_for_display_line",
    "handle_preview_click",
    "SourcePaneClickResult",
    "SourcePaneMouseHandlers",
    "SourcePaneGeometry",
    "copy_selected_source_range",
]
