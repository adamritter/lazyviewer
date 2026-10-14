"""Optional callback bundle for integrations and loop tests.

The application itself supplies the App object directly.
"""
from __future__ import annotations
from collections.abc import Callable
from dataclasses import dataclass

@dataclass(frozen=True)
class RuntimeLoopCallbacks:
    """UI-root contract used by ``run_main_loop`` in tests and adapters."""

    tree_pane: object
    source_pane: object
    layout: object
    maybe_refresh_tree_watch: Callable[[], None]
    maybe_refresh_git_watch: Callable[[], None]
    refresh_git_status_overlay: Callable[..., None]
    handle_normal_key: Callable[[str, int], bool]
    save_left_pane_width: Callable[[int, int], None]
    handle_tree_mouse_wheel: Callable[[str], bool] | None = None
    handle_tree_mouse_click: Callable[[str], bool] | None = None
    handle_picker_key: Callable[[str, float], tuple[bool, bool]] | None = None
    handle_tree_filter_key: Callable[[str], bool] | None = None
    tick_source_selection_drag: Callable[[], None] | None = None
    tick_tree_filter_search: Callable[[float], bool] | None = None
    maybe_poll_directory_preview_results: Callable[[], bool] | None = None
    maybe_prefetch_directory_preview: Callable[[], bool] | None = None
    after_first_frame: Callable[[], None] | None = None
