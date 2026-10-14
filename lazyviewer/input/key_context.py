"""Legacy explicit context adapter for normal-mode key handling."""
from __future__ import annotations
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from .key_common import default_max_horizontal_text_offset
from .key_normal import _NormalKeyContextBase
from ..runtime.state import AppState
from ..runtime.navigation import JumpLocation

@dataclass(frozen=True)
class NormalKeyContext(_NormalKeyContextBase):
    """State and bound operations required for normal-mode key handling."""

    state: AppState
    current_jump_location: Callable[[], JumpLocation]
    record_jump_if_changed: Callable[[JumpLocation], None]
    open_symbol_picker: Callable[[], None]
    reroot_to_parent: Callable[[], None]
    reroot_to_selected_target: Callable[[], None]
    toggle_hidden_files: Callable[[], None]
    toggle_tree_pane: Callable[[], None]
    toggle_wrap_mode: Callable[[], None]
    toggle_tree_size_labels: Callable[[], None]
    toggle_help_panel: Callable[[], None]
    toggle_git_features: Callable[[], None]
    launch_lazygit: Callable[[], None]
    handle_tree_mouse_wheel: Callable[[str], bool]
    handle_tree_mouse_click: Callable[[str], bool]
    move_tree_selection: Callable[[int], bool]
    rebuild_tree_entries: Callable[..., None]
    preview_selected_entry: Callable[..., None]
    refresh_rendered_for_current_path: Callable[..., None]
    refresh_git_status_overlay: Callable[..., None]
    maybe_grow_directory_preview: Callable[[], bool]
    visible_content_rows: Callable[[], int]
    rebuild_screen_lines: Callable[..., None]
    mark_tree_watch_dirty: Callable[[], None]
    launch_editor_for_path: Callable[[Path], str | None]
    jump_to_next_git_modified: Callable[[int], bool]
    max_horizontal_text_offset: Callable[[], int] = default_max_horizontal_text_offset
