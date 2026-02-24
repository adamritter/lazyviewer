"""Normal-mode keyboard handling."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..runtime.navigation import JumpLocation
from ..runtime.state import AppState
from ..tree_model import (
    next_directory_entry_index,
    next_index_after_directory_subtree,
    next_opened_directory_entry_index,
)
from .key_common import default_max_horizontal_text_offset, effective_max_start
from .key_registry import KeyComboBinding, KeyComboRegistry


@dataclass(frozen=True)
class NormalKeyContext:
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


class NormalKeyHandler:
    """Reusable normal-mode handler with bound runtime dependencies."""

    def __init__(self, context: NormalKeyContext) -> None:
        self.context = context

    def handle(self, key: str, term_columns: int) -> bool:
        """Handle one normal-mode key and return ``True`` when app should quit."""
        return handle_normal_key(key, term_columns, self.context)


def handle_normal_key(
    key: str,
    term_columns: int,
    context: NormalKeyContext,
) -> bool:
    """Handle one normal-mode key and return ``True`` when app should quit."""
    state = context.state
    current_jump_location = context.current_jump_location
    record_jump_if_changed = context.record_jump_if_changed
    open_symbol_picker = context.open_symbol_picker
    reroot_to_parent = context.reroot_to_parent
    reroot_to_selected_target = context.reroot_to_selected_target
    toggle_hidden_files = context.toggle_hidden_files
    toggle_tree_pane = context.toggle_tree_pane
    toggle_wrap_mode = context.toggle_wrap_mode
    toggle_tree_size_labels = context.toggle_tree_size_labels
    toggle_help_panel = context.toggle_help_panel
    toggle_git_features = context.toggle_git_features
    launch_lazygit = context.launch_lazygit
    handle_tree_mouse_wheel = context.handle_tree_mouse_wheel
    handle_tree_mouse_click = context.handle_tree_mouse_click
    move_tree_selection = context.move_tree_selection
    rebuild_tree_entries = context.rebuild_tree_entries
    preview_selected_entry = context.preview_selected_entry
    refresh_rendered_for_current_path = context.refresh_rendered_for_current_path
    refresh_git_status_overlay = context.refresh_git_status_overlay
    maybe_grow_directory_preview = context.maybe_grow_directory_preview
    visible_content_rows = context.visible_content_rows
    rebuild_screen_lines = context.rebuild_screen_lines
    mark_tree_watch_dirty = context.mark_tree_watch_dirty
    launch_editor_for_path = context.launch_editor_for_path
    jump_to_next_git_modified = context.jump_to_next_git_modified
    max_horizontal_text_offset = context.max_horizontal_text_offset
    key_lower = key.lower()

    def set_directory_expanded_state(resolved: Path, expanded: bool) -> None:
        """Toggle expansion for ``resolved`` in tree and content-filter overlays."""
        if state.tree_filter_active and state.tree_filter_mode == "content":
            if expanded:
                state.tree_filter_collapsed_dirs.discard(resolved)
            elif resolved != state.tree_root:
                state.tree_filter_collapsed_dirs.add(resolved)
        if expanded:
            state.expanded.add(resolved)
        else:
            state.expanded.discard(resolved)

    def refresh_tree_after_directory_change(resolved: Path) -> None:
        """Rebuild tree and preview after expand/collapse state mutation."""
        rebuild_tree_entries(preferred_path=resolved)
        mark_tree_watch_dirty()
        preview_selected_entry()
        state.dirty = True

    def open_symbol_picker_action() -> bool | None:
        """Open symbol picker unless already active, clearing pending count."""
        if state.picker_active:
            return None
        state.count_buffer = ""
        open_symbol_picker()
        return False

    def begin_mark_set_action() -> bool:
        """Enter named-mark set mode for next keypress."""
        state.count_buffer = ""
        state.pending_mark_set = True
        state.pending_mark_jump = False
        return False

    def begin_mark_jump_action() -> bool:
        """Enter named-mark jump mode for next keypress."""
        state.count_buffer = ""
        state.pending_mark_set = False
        state.pending_mark_jump = True
        return False

    pre_exact_bindings = KeyComboRegistry().register_bindings(
        KeyComboBinding(("m",), begin_mark_set_action),
        KeyComboBinding(("'",), begin_mark_jump_action),
        KeyComboBinding(("s",), open_symbol_picker_action),
    )

    handled = pre_exact_bindings.dispatch(key)
    if handled is not None:
        return handled

    if key.isdigit():
        state.count_buffer += key
        return False

    count = int(state.count_buffer) if state.count_buffer else None
    state.count_buffer = ""

    def toggle_help_panel_action() -> bool:
        """Toggle help overlay from normal mode."""
        toggle_help_panel()
        return False

    def launch_lazygit_action() -> bool:
        """Launch lazygit integration command."""
        launch_lazygit()
        return False

    def toggle_git_features_action() -> bool:
        """Enable or disable git-aware overlays and key behavior."""
        toggle_git_features()
        return False

    global_exact_bindings = KeyComboRegistry().register_bindings(
        KeyComboBinding(("?", "CTRL_QUESTION"), toggle_help_panel_action),
        KeyComboBinding(("CTRL_G",), launch_lazygit_action),
        KeyComboBinding(("CTRL_O",), toggle_git_features_action),
    )
    handled = global_exact_bindings.dispatch(key)
    if handled is not None:
        return handled

    if key in {"CTRL_U", "CTRL_D"}:
        if state.browser_visible and state.tree_entries:
            direction = -1 if key == "CTRL_U" else 1
            jump_steps = 1 if count is None else max(1, min(10, count))

            def parent_directory_index(from_idx: int) -> int | None:
                """Return nearest ancestor directory index above ``from_idx``."""
                current_depth = state.tree_entries[from_idx].depth
                idx = from_idx - 1
                while idx >= 0:
                    candidate = state.tree_entries[idx]
                    if candidate.is_dir and candidate.depth < current_depth:
                        return idx
                    idx -= 1
                return None

            def smart_directory_jump(from_idx: int, jump_direction: int) -> int | None:
                """Compute contextual ctrl-u/ctrl-d tree jump destination."""
                if jump_direction < 0:
                    prev_opened = next_opened_directory_entry_index(
                        state.tree_entries,
                        from_idx,
                        -1,
                        state.expanded,
                    )
                    if prev_opened is not None:
                        return prev_opened
                    return parent_directory_index(from_idx)

                current_entry = state.tree_entries[from_idx]
                if current_entry.is_dir and current_entry.path.resolve() in state.expanded:
                    after_current = next_index_after_directory_subtree(state.tree_entries, from_idx)
                    if after_current is not None:
                        return after_current

                next_opened = next_opened_directory_entry_index(
                    state.tree_entries,
                    from_idx,
                    1,
                    state.expanded,
                )
                if next_opened is not None:
                    after_next_opened = next_index_after_directory_subtree(state.tree_entries, next_opened)
                    if after_next_opened is not None:
                        return after_next_opened
                    return next_opened

                return next_directory_entry_index(state.tree_entries, from_idx, 1)

            target_idx = state.selected_idx
            moved = 0
            while moved < jump_steps:
                next_idx = smart_directory_jump(target_idx, direction)
                if next_idx is None:
                    break
                target_idx = next_idx
                moved += 1
            if moved > 0:
                origin = current_jump_location()
                prev_selected = state.selected_idx
                state.selected_idx = target_idx
                preview_selected_entry()
                record_jump_if_changed(origin)
                if state.selected_idx != prev_selected or current_jump_location() != origin:
                    state.dirty = True
        return False

    def reroot_to_parent_action() -> bool:
        """Reroot file tree at parent directory."""
        reroot_to_parent()
        return False

    def reroot_to_selected_target_action() -> bool:
        """Reroot file tree at selected entry or its parent directory."""
        reroot_to_selected_target()
        return False

    def toggle_hidden_files_action() -> bool:
        """Toggle hidden-file visibility in tree pane."""
        toggle_hidden_files()
        return False

    def toggle_tree_pane_action() -> bool:
        """Toggle tree-pane visibility."""
        toggle_tree_pane()
        return False

    def toggle_wrap_mode_action() -> bool:
        """Toggle wrapped source rendering mode."""
        toggle_wrap_mode()
        return False

    def toggle_tree_size_labels_action() -> bool:
        """Toggle directory size labels in tree entries."""
        toggle_tree_size_labels()
        return False

    def edit_selected_target_action() -> bool:
        """Open selected path in editor and refresh preview/tree state."""
        edit_target: Path | None = None
        if state.browser_visible and state.tree_entries:
            selected_entry = state.tree_entries[state.selected_idx]
            edit_target = selected_entry.path.resolve()
        if edit_target is None:
            edit_target = state.current_path.resolve()

        error = launch_editor_for_path(edit_target)
        state.current_path = edit_target
        if error is None:
            if edit_target.is_dir():
                rebuild_tree_entries(preferred_path=edit_target)
                mark_tree_watch_dirty()
            refresh_rendered_for_current_path(
                reset_scroll=True,
                reset_dir_budget=True,
                force_rebuild=True,
            )
            refresh_git_status_overlay(force=True)
        else:
            state.rendered = f"\033[31m{error}\033[0m"
            rebuild_screen_lines(columns=term_columns, preserve_scroll=False)
            state.text_x = 0
            state.dir_preview_path = None
            state.dir_preview_truncated = False
            state.preview_image_path = None
            state.preview_image_format = None
            state.preview_is_git_diff = False
        state.dirty = True
        return False

    def quit_action() -> bool:
        """Signal application shutdown."""
        return True

    def jump_to_next_git_modified_action(direction: int) -> bool | None:
        """Jump to next/previous git-modified entry when feature is enabled."""
        if state.tree_filter_active or not state.git_features_enabled:
            return None
        if jump_to_next_git_modified(direction):
            state.dirty = True
        return False

    mode_exact_bindings = KeyComboRegistry().register_bindings(
        KeyComboBinding(("R",), reroot_to_parent_action),
        KeyComboBinding(("S",), toggle_tree_size_labels_action),
        KeyComboBinding(("r",), reroot_to_selected_target_action),
        KeyComboBinding((".",), toggle_hidden_files_action),
        KeyComboBinding(("n",), lambda: jump_to_next_git_modified_action(1)),
        KeyComboBinding(("N", "p"), lambda: jump_to_next_git_modified_action(-1)),
        KeyComboBinding(("\x03",), quit_action),
    )
    mode_lower_bindings = KeyComboRegistry(normalize=str.lower).register_bindings(
        KeyComboBinding(("t",), toggle_tree_pane_action),
        KeyComboBinding(("w",), toggle_wrap_mode_action),
        KeyComboBinding(("e",), edit_selected_target_action),
        KeyComboBinding(("q",), quit_action),
    )
    handled = mode_exact_bindings.dispatch(key)
    if handled is not None:
        return handled
    handled = mode_lower_bindings.dispatch(key)
    if handled is not None:
        return handled

    if handle_tree_mouse_wheel(key):
        return False
    if handle_tree_mouse_click(key):
        return False

    def move_tree_down_action() -> bool:
        """Move tree selection down one entry."""
        if move_tree_selection(1):
            state.dirty = True
        return False

    def move_tree_up_action() -> bool:
        """Move tree selection up one entry."""
        if move_tree_selection(-1):
            state.dirty = True
        return False

    def open_tree_entry_action() -> bool:
        """Open selected tree entry, expanding dirs or previewing files."""
        entry = state.tree_entries[state.selected_idx]
        if entry.is_dir:
            resolved = entry.path.resolve()
            if resolved not in state.expanded:
                set_directory_expanded_state(resolved, True)
                refresh_tree_after_directory_change(resolved)
            else:
                next_idx = state.selected_idx + 1
                if next_idx < len(state.tree_entries) and state.tree_entries[next_idx].depth > entry.depth:
                    state.selected_idx = next_idx
                    preview_selected_entry()
                    state.dirty = True
        else:
            origin = current_jump_location()
            state.current_path = entry.path.resolve()
            refresh_rendered_for_current_path(reset_scroll=True, reset_dir_budget=True)
            record_jump_if_changed(origin)
            state.dirty = True
        return False

    def close_or_parent_tree_entry_action() -> bool:
        """Collapse selected directory or move selection to parent directory."""
        entry = state.tree_entries[state.selected_idx]
        if (
            entry.is_dir
            and entry.path.resolve() in state.expanded
            and entry.path.resolve() != state.tree_root
        ):
            resolved = entry.path.resolve()
            set_directory_expanded_state(resolved, False)
            refresh_tree_after_directory_change(resolved)
        elif entry.path.resolve() != state.tree_root:
            parent = entry.path.parent.resolve()
            for idx, candidate in enumerate(state.tree_entries):
                if candidate.path.resolve() == parent:
                    state.selected_idx = idx
                    preview_selected_entry()
                    state.dirty = True
                    break
        return False

    def toggle_directory_tree_entry_action() -> bool | None:
        """Toggle expand/collapse on selected directory tree entry."""
        entry = state.tree_entries[state.selected_idx]
        if not entry.is_dir:
            return None
        resolved = entry.path.resolve()
        if resolved in state.expanded:
            if resolved != state.tree_root:
                set_directory_expanded_state(resolved, False)
        else:
            set_directory_expanded_state(resolved, True)
        refresh_tree_after_directory_change(resolved)
        return False

    if state.browser_visible:
        browser_lower_bindings = KeyComboRegistry(normalize=str.lower).register_bindings(
            KeyComboBinding(("j",), move_tree_down_action),
            KeyComboBinding(("k",), move_tree_up_action),
            KeyComboBinding(("l",), open_tree_entry_action),
            KeyComboBinding(("h",), close_or_parent_tree_entry_action),
        )
        browser_exact_bindings = KeyComboRegistry().register_bindings(
            KeyComboBinding(("ENTER",), toggle_directory_tree_entry_action),
        )
        handled = browser_lower_bindings.dispatch(key)
        if handled is not None:
            return handled
        handled = browser_exact_bindings.dispatch(key)
        if handled is not None:
            return handled

    prev_start = state.start
    prev_text_x = state.text_x
    scrolling_down = False
    page_rows = visible_content_rows()
    max_start = effective_max_start(state, page_rows)
    if key == " " or key_lower == "f":
        pages = count if count is not None else 1
        state.start += page_rows * max(1, pages)
        scrolling_down = True
    elif key_lower == "d":
        mult = count if count is not None else 1
        state.start += max(1, page_rows // 2) * max(1, mult)
        scrolling_down = True
    elif key_lower == "u":
        mult = count if count is not None else 1
        state.start -= max(1, page_rows // 2) * max(1, mult)
    elif key == "DOWN" or (not state.browser_visible and key_lower == "j"):
        state.start += count if count is not None else 1
        scrolling_down = True
    elif key == "UP" or (not state.browser_visible and key_lower == "k"):
        state.start -= count if count is not None else 1
    elif key == "g":
        if count is None:
            state.start = 0
        else:
            state.start = max(0, min(count - 1, state.max_start))
    elif key == "G":
        if count is None:
            state.start = max_start
        else:
            state.start = max(0, min(count - 1, max_start))
        scrolling_down = True
    elif key == "ENTER":
        state.start += count if count is not None else 1
        scrolling_down = True
    elif key == "B":
        pages = count if count is not None else 1
        state.start -= page_rows * max(1, pages)
    elif (key == "LEFT" or (not state.browser_visible and key_lower == "h")) and not state.wrap_text:
        step = count if count is not None else 4
        state.text_x = max(0, state.text_x - max(1, step))
    elif (key == "RIGHT" or (not state.browser_visible and key_lower == "l")) and not state.wrap_text:
        step = count if count is not None else 4
        state.text_x = min(max_horizontal_text_offset(), state.text_x + max(1, step))
    elif key == "HOME":
        state.start = 0
    elif key == "END":
        state.start = max_start
    elif key == "ESC":
        return True

    state.start = max(0, min(state.start, max_start))
    state.max_start = max(state.max_start, max_start)
    grew_preview = scrolling_down and maybe_grow_directory_preview()
    if state.start != prev_start or state.text_x != prev_text_x or grew_preview:
        state.dirty = True
    return False
