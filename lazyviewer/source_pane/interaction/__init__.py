"""Source-pane interaction modules."""

def __getattr__(name):
    if name in {"directory_preview_target_for_display_line", "handle_preview_click"}:
        from . import events
        return getattr(events, name)
    if name in {"SourcePaneClickResult", "SourcePaneMouseHandlers"}:
        from . import mouse
        return getattr(mouse, name)
    if name in {"SourcePaneGeometry", "copy_selected_source_range"}:
        from . import geometry
        return getattr(geometry, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "directory_preview_target_for_display_line",
    "handle_preview_click",
    "SourcePaneClickResult",
    "SourcePaneMouseHandlers",
    "SourcePaneGeometry",
    "copy_selected_source_range",
]
