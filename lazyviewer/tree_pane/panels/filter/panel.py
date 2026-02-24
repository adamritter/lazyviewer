"""Tree filter panel UI element owning lifecycle and key handling."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ....runtime.navigation import JumpLocation
    from .controller import TreeFilterController


class FilterPanel:
    """Stateful UI element for tree filter interactions."""

    def __init__(self, owner: TreeFilterController) -> None:
        self.owner = owner

    def toggle_mode(self, mode: str) -> None:
        """Open, switch, or close filter UI based on current prompt mode."""
        if self.owner.state.tree_filter_active:
            if self.owner.state.tree_filter_mode == mode and self.owner.state.tree_filter_editing:
                self.owner.close_tree_filter(clear_query=True)
            elif self.owner.state.tree_filter_mode != mode:
                self.owner.open_tree_filter(mode)
            else:
                self.owner.state.tree_filter_editing = True
                self.owner.state.dirty = True
            return
        self.owner.open_tree_filter(mode)

    def open(self, mode: str = "files") -> None:
        """Open filter panel in requested mode and initialize session fields."""
        was_active = self.owner.state.tree_filter_active
        previous_mode = self.owner.state.tree_filter_mode
        if not self.owner.state.tree_filter_active:
            self.owner.state.tree_filter_prev_browser_visible = self.owner.state.browser_visible
        was_browser_visible = self.owner.state.browser_visible
        self.owner.state.browser_visible = True
        if self.owner.state.wrap_text and not was_browser_visible:
            self.owner.rebuild_screen_lines()
        self.owner.state.tree_filter_active = True
        self.owner.set_tree_filter_prompt_row_visible(True)
        self.owner.state.tree_filter_mode = mode
        self.owner.state.tree_filter_editing = True
        self.owner.state.tree_filter_origin = self.owner.current_jump_location() if mode == "content" else None
        self.owner.state.tree_filter_query = ""
        self.owner.state.tree_filter_match_count = 0
        self.owner.state.tree_filter_truncated = False
        self.owner.reset_tree_filter_session_state()
        if was_active and previous_mode != mode:
            self.owner.rebuild_tree_entries(preferred_path=self.owner.state.current_path.resolve())
        self.owner.state.dirty = True
        if self.owner.on_tree_filter_state_change is not None:
            self.owner.on_tree_filter_state_change()

    def close(
        self,
        clear_query: bool = True,
        restore_origin: bool = False,
    ) -> None:
        """Close filter panel, optionally restoring original content-search position."""
        previous_browser_visible = self.owner.state.tree_filter_prev_browser_visible
        restore_location: JumpLocation | None = None
        if (
            restore_origin
            and self.owner.state.tree_filter_mode == "content"
            and self.owner.state.tree_filter_origin is not None
        ):
            restore_location = self.owner.state.tree_filter_origin.normalized()
        self.owner.state.tree_filter_active = False
        self.owner.state.tree_filter_editing = False
        self.owner.set_tree_filter_prompt_row_visible(True)
        self.owner.state.tree_filter_mode = "files"
        if clear_query:
            self.owner.state.tree_filter_query = ""
            self.owner.state.tree_filter_truncated = False
        self.owner.reset_tree_filter_session_state()
        self.owner.state.tree_filter_prev_browser_visible = None
        if previous_browser_visible is not None:
            browser_visibility_changed = self.owner.state.browser_visible != previous_browser_visible
            self.owner.state.browser_visible = previous_browser_visible
            if self.owner.state.wrap_text and browser_visibility_changed:
                self.owner.rebuild_screen_lines()
        if restore_location is not None:
            self.owner.jump_to_path(restore_location.path)
            self.owner.state.max_start = max(0, len(self.owner.state.lines) - self.owner.visible_content_rows())
            self.owner.state.start = max(0, min(restore_location.start, self.owner.state.max_start))
            self.owner.state.text_x = 0 if self.owner.state.wrap_text else max(0, restore_location.text_x)
        else:
            self.owner.rebuild_tree_entries(preferred_path=self.owner.state.current_path.resolve())
        self.owner.state.tree_filter_origin = None
        self.owner.state.dirty = True
        if self.owner.on_tree_filter_state_change is not None:
            self.owner.on_tree_filter_state_change()

    def activate_selection(self) -> None:
        """Activate selected filter result according to current filter mode."""
        if not self.owner.state.tree_entries:
            if self.owner.state.tree_filter_mode == "content":
                self.owner.state.tree_filter_editing = False
                self.owner.state.dirty = True
            else:
                self.owner.close_tree_filter(clear_query=True)
            return

        entry = self.owner.state.tree_entries[self.owner.state.selected_idx]
        if entry.is_dir:
            candidate_idx = self.owner.nearest_tree_filter_result_entry_index(self.owner.state.selected_idx)
            if candidate_idx is None:
                self.owner.close_tree_filter(clear_query=True)
                return
            self.owner.state.selected_idx = candidate_idx
            entry = self.owner.state.tree_entries[self.owner.state.selected_idx]

        selected_path = entry.path.resolve()
        selected_line = entry.line if entry.kind == "search_hit" else None
        if self.owner.state.tree_filter_mode == "content":
            origin = self.owner.current_jump_location()
            self.owner.state.tree_filter_editing = False
            self.owner.preview_selected_entry()
            self.owner.record_jump_if_changed(origin)
            self.owner.state.dirty = True
            return

        origin = self.owner.current_jump_location()
        self.owner.close_tree_filter(clear_query=True)
        self.owner.jump_to_path(selected_path)
        if selected_line is not None:
            self.owner.jump_to_line(max(0, selected_line - 1))
        self.owner.record_jump_if_changed(origin)
        self.owner.state.dirty = True

    def handle_key(
        self,
        key: str,
        *,
        handle_tree_mouse_wheel: Callable[[str], bool],
        handle_tree_mouse_click: Callable[[str], bool],
        toggle_help_panel: Callable[[], None],
    ) -> bool:
        """Handle one key for tree-filter prompt, list navigation, and hit jumps."""
        if not self.owner.state.tree_filter_active:
            return False

        def apply_live_filter_query(query: str) -> None:
            content_mode = self.owner.state.tree_filter_mode == "content"
            self.owner.apply_tree_filter_query(
                query,
                preview_selection=not content_mode,
                select_first_file=not content_mode,
            )

        if self.owner.state.tree_filter_editing:
            if handle_tree_mouse_wheel(key):
                return True
            if handle_tree_mouse_click(key):
                return True
            if key == "ESC":
                self.owner.close_tree_filter(
                    clear_query=True,
                    restore_origin=self.owner.state.tree_filter_mode == "content",
                )
                return True
            if key == "ENTER":
                self.owner.activate_tree_filter_selection()
                return True
            if key == "TAB":
                self.owner.state.tree_filter_editing = False
                self.owner.state.dirty = True
                return True
            if key == "UP" or key == "CTRL_K":
                if self.owner.move_tree_selection(-1):
                    self.owner.state.dirty = True
                return True
            if key == "DOWN" or key == "CTRL_J":
                if self.owner.move_tree_selection(1):
                    self.owner.state.dirty = True
                return True
            if key == "BACKSPACE":
                if self.owner.state.tree_filter_query:
                    apply_live_filter_query(self.owner.state.tree_filter_query[:-1])
                return True
            if key == "CTRL_U":
                if self.owner.state.tree_filter_query:
                    apply_live_filter_query("")
                return True
            if key == "CTRL_QUESTION":
                toggle_help_panel()
                return True
            if len(key) == 1 and key.isprintable():
                apply_live_filter_query(self.owner.state.tree_filter_query + key)
                return True
            return True

        if key == "TAB":
            self.owner.state.tree_filter_editing = True
            self.owner.set_tree_filter_prompt_row_visible(True)
            self.owner.state.dirty = True
            return True
        if key == "ENTER":
            self.owner.activate_tree_filter_selection()
            return True
        if key == "ESC":
            self.owner.close_tree_filter(clear_query=True)
            return True
        if self.owner.state.tree_filter_mode == "content":
            if key == "n":
                if self.owner.jump_to_next_content_hit(1):
                    self.owner.state.dirty = True
                return True
            if key in {"N", "p"}:
                if self.owner.jump_to_next_content_hit(-1):
                    self.owner.state.dirty = True
                return True
        return False
