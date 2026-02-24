"""Navigation, picker, and jump-history operations over ``AppState``.

The controller centralizes command palette and symbol picker behavior together
with navigation side effects such as rerooting, wrap/tree toggles, and mark/
jump-history updates. Runtime wiring injects rendering and tree refresh hooks
so this logic stays deterministic and testable.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..runtime.config import save_named_marks, save_show_hidden
from ..runtime.navigation import JumpLocation, is_named_mark_key
from ..search.fuzzy import fuzzy_match_labels
from ..runtime.state import AppState
from ..source_pane.symbols import collect_symbols

PICKER_RESULT_LIMIT = 200


def _line_has_newline_terminator(line: str) -> bool:
    """Return whether a rendered display fragment ends a source line."""
    return line.endswith("\n") or line.endswith("\r")


def _source_line_for_display_index(lines: list[str], display_index: int) -> int:
    """Map a rendered-line index back to 1-based source line numbering."""
    if not lines:
        return 1

    clamped = max(0, min(display_index, len(lines) - 1))
    source_line = 1
    for idx in range(clamped):
        if _line_has_newline_terminator(lines[idx]):
            source_line += 1
    return source_line


def _first_display_index_for_source_line(lines: list[str], source_line: int) -> int:
    """Return the first rendered-line index corresponding to ``source_line``."""
    if not lines:
        return 0

    target = max(1, source_line)
    current_source = 1
    for idx, line in enumerate(lines):
        if current_source >= target:
            return idx
        if _line_has_newline_terminator(line):
            current_source += 1
    return len(lines) - 1


@dataclass(frozen=True)
class NavigationPickerDeps:
    """Runtime dependencies required by :class:`NavigationPickerOps`."""

    state: AppState
    command_palette_items: tuple[tuple[str, str], ...]
    rebuild_screen_lines: Callable[..., None]
    rebuild_tree_entries: Callable[..., None]
    preview_selected_entry: Callable[..., None]
    schedule_tree_filter_index_warmup: Callable[[], None]
    mark_tree_watch_dirty: Callable[[], None]
    reset_git_watch_context: Callable[[], None]
    refresh_git_status_overlay: Callable[..., None]
    visible_content_rows: Callable[[], int]
    refresh_rendered_for_current_path: Callable[..., None]


class NavigationPickerOps:
    """State-bound picker/navigation operations used by key handlers."""

    def __init__(self, deps: NavigationPickerDeps) -> None:
        """Bind controller methods to app state and injected runtime hooks."""
        self.state = deps.state
        self.command_palette_items = deps.command_palette_items
        self.rebuild_screen_lines = deps.rebuild_screen_lines
        self.rebuild_tree_entries = deps.rebuild_tree_entries
        self.preview_selected_entry = deps.preview_selected_entry
        self.schedule_tree_filter_index_warmup = deps.schedule_tree_filter_index_warmup
        self.mark_tree_watch_dirty = deps.mark_tree_watch_dirty
        self.reset_git_watch_context = deps.reset_git_watch_context
        self.refresh_git_status_overlay = deps.refresh_git_status_overlay
        self.visible_content_rows = deps.visible_content_rows
        self.refresh_rendered_for_current_path = deps.refresh_rendered_for_current_path
        self.open_tree_filter_fn: Callable[[str], None] | None = None

    def set_open_tree_filter(self, callback: Callable[[str], None]) -> None:
        """Register callback used by command palette filter/search actions."""
        self.open_tree_filter_fn = callback

    def refresh_symbol_picker_matches(self, reset_selection: bool = False) -> None:
        """Recompute visible symbol matches from current picker query."""
        matched = fuzzy_match_labels(
            self.state.picker_query,
            self.state.picker_symbol_labels,
            limit=PICKER_RESULT_LIMIT,
        )
        self.state.picker_matches = []
        self.state.picker_match_labels = [label for _, label, _ in matched]
        self.state.picker_match_lines = [self.state.picker_symbol_lines[idx] for idx, _, _ in matched]
        self.state.picker_match_commands = []
        if self.state.picker_match_labels:
            self.state.picker_message = ""
        elif not self.state.picker_message:
            self.state.picker_message = " no matching symbols"
        self.state.picker_selected = 0 if reset_selection else max(
            0,
            min(self.state.picker_selected, max(0, len(self.state.picker_match_labels) - 1)),
        )
        if reset_selection or not self.state.picker_match_labels:
            self.state.picker_list_start = 0

    def refresh_command_picker_matches(self, reset_selection: bool = False) -> None:
        """Recompute command palette matches from current picker query."""
        matched = fuzzy_match_labels(
            self.state.picker_query,
            self.state.picker_command_labels,
            limit=PICKER_RESULT_LIMIT,
        )
        self.state.picker_matches = []
        self.state.picker_match_labels = [label for _, label, _ in matched]
        self.state.picker_match_lines = []
        self.state.picker_match_commands = [self.state.picker_command_ids[idx] for idx, _, _ in matched]
        if self.state.picker_match_labels:
            self.state.picker_message = ""
        elif not self.state.picker_message:
            self.state.picker_message = " no matching commands"
        self.state.picker_selected = 0 if reset_selection else max(
            0,
            min(self.state.picker_selected, max(0, len(self.state.picker_match_labels) - 1)),
        )
        if reset_selection or not self.state.picker_match_labels:
            self.state.picker_list_start = 0

    def refresh_active_picker_matches(self, reset_selection: bool = False) -> None:
        """Refresh matches for whichever picker mode is currently active."""
        if self.state.picker_mode == "commands":
            self.refresh_command_picker_matches(reset_selection=reset_selection)
            return
        self.refresh_symbol_picker_matches(reset_selection=reset_selection)

    def resolve_symbol_target(self) -> Path | None:
        """Resolve file path whose symbols should populate the symbol picker."""
        if self.state.current_path.is_file():
            return self.state.current_path.resolve()
        if not self.state.tree_entries:
            return None
        entry = self.state.tree_entries[self.state.selected_idx]
        if entry.is_dir or not entry.path.is_file():
            return None
        return entry.path.resolve()

    def open_symbol_picker(self) -> None:
        """Enter symbol-picker mode and populate symbols for current file target."""
        if not self.state.picker_active:
            self.state.picker_prev_browser_visible = self.state.browser_visible
        self.state.picker_active = True
        self.state.picker_mode = "symbols"
        self.state.picker_focus = "query"
        self.state.picker_message = ""
        self.state.picker_query = ""
        self.state.picker_selected = 0
        self.state.picker_list_start = 0
        self.state.picker_matches = []
        self.state.picker_match_labels = []
        self.state.picker_match_lines = []
        self.state.picker_match_commands = []
        self.state.picker_command_ids = []
        self.state.picker_command_labels = []
        was_browser_visible = self.state.browser_visible
        self.state.browser_visible = True
        if self.state.wrap_text and not was_browser_visible:
            self.rebuild_screen_lines()

        target = self.resolve_symbol_target()
        self.state.picker_symbol_file = target
        self.state.picker_symbol_labels = []
        self.state.picker_symbol_lines = []
        if target is None:
            self.state.picker_message = " no file selected"
            self.state.dirty = True
            return

        symbols, error = collect_symbols(target)
        if error:
            self.state.picker_message = f" {error}"
            self.state.dirty = True
            return

        self.state.picker_symbol_labels = [symbol.label for symbol in symbols]
        self.state.picker_symbol_lines = [symbol.line for symbol in symbols]
        if not self.state.picker_symbol_labels:
            self.state.picker_message = " no functions/classes/imports found"
            self.state.dirty = True
            return

        self.refresh_symbol_picker_matches(reset_selection=True)
        self.state.dirty = True

    def open_command_picker(self) -> None:
        """Enter command-palette mode and load command label/id lists."""
        if not self.state.picker_active:
            self.state.picker_prev_browser_visible = self.state.browser_visible
        self.state.picker_active = True
        self.state.picker_mode = "commands"
        self.state.picker_focus = "tree"
        self.state.picker_message = ""
        self.state.picker_query = ""
        self.state.picker_selected = 0
        self.state.picker_list_start = 0
        self.state.picker_matches = []
        self.state.picker_match_labels = []
        self.state.picker_match_lines = []
        self.state.picker_match_commands = []
        self.state.picker_symbol_file = None
        self.state.picker_symbol_labels = []
        self.state.picker_symbol_lines = []
        self.state.picker_command_ids = [command_id for command_id, _ in self.command_palette_items]
        self.state.picker_command_labels = [label for _, label in self.command_palette_items]
        was_browser_visible = self.state.browser_visible
        self.state.browser_visible = True
        if self.state.wrap_text and not was_browser_visible:
            self.rebuild_screen_lines()

        self.refresh_command_picker_matches(reset_selection=True)
        self.state.dirty = True

    def close_picker(self, reset_query: bool = True) -> None:
        """Close picker UI and restore non-picker browser visibility state."""
        previous_browser_visible = self.state.picker_prev_browser_visible
        self.state.picker_active = False
        if reset_query:
            self.state.picker_query = ""
        self.state.picker_mode = "symbols"
        self.state.picker_focus = "query"
        self.state.picker_message = ""
        self.state.picker_selected = 0
        self.state.picker_list_start = 0
        self.state.picker_matches = []
        self.state.picker_match_labels = []
        self.state.picker_match_lines = []
        self.state.picker_match_commands = []
        self.state.picker_symbol_file = None
        self.state.picker_symbol_labels = []
        self.state.picker_symbol_lines = []
        self.state.picker_command_ids = []
        self.state.picker_command_labels = []
        self.state.picker_prev_browser_visible = None
        if previous_browser_visible is not None:
            browser_visibility_changed = self.state.browser_visible != previous_browser_visible
            self.state.browser_visible = previous_browser_visible
            if self.state.wrap_text and browser_visibility_changed:
                self.rebuild_screen_lines()
        self.state.dirty = True

    def current_term_columns(self) -> int:
        """Return current terminal width used for render rebuild calls."""
        return shutil.get_terminal_size((80, 24)).columns

    def reroot_to_parent(self) -> None:
        """Move tree root to parent directory, preserving previous root expanded."""
        old_root = self.state.tree_root.resolve()
        parent_root = old_root.parent.resolve()
        if parent_root == old_root:
            return
        self.state.tree_root = parent_root
        self.state.expanded = {self.state.tree_root, old_root}
        self.rebuild_tree_entries(preferred_path=old_root, center_selection=True)
        self.preview_selected_entry(force=True)
        self.schedule_tree_filter_index_warmup()
        self.mark_tree_watch_dirty()
        self.reset_git_watch_context()
        self.refresh_git_status_overlay(force=True)
        self.state.dirty = True

    def reroot_to_selected_target(self) -> None:
        """Reroot tree at selected entry (or current path fallback) directory."""
        selected_entry = (
            self.state.tree_entries[self.state.selected_idx]
            if self.state.tree_entries and 0 <= self.state.selected_idx < len(self.state.tree_entries)
            else None
        )
        if selected_entry is not None:
            selected_target = selected_entry.path.resolve()
            target_root = selected_target if selected_entry.is_dir else selected_target.parent.resolve()
        else:
            selected_target = self.state.current_path.resolve()
            target_root = selected_target if selected_target.is_dir() else selected_target.parent.resolve()

        old_root = self.state.tree_root.resolve()
        if target_root == old_root:
            return
        self.state.tree_root = target_root
        self.state.expanded = {self.state.tree_root}
        self.rebuild_tree_entries(preferred_path=selected_target, center_selection=True)
        self.preview_selected_entry(force=True)
        self.schedule_tree_filter_index_warmup()
        self.mark_tree_watch_dirty()
        self.reset_git_watch_context()
        self.refresh_git_status_overlay(force=True)
        self.state.dirty = True

    def toggle_hidden_files(self) -> None:
        """Toggle hidden-file visibility and persist preference to config."""
        self.state.show_hidden = not self.state.show_hidden
        save_show_hidden(self.state.show_hidden)
        selected_path = self.state.tree_entries[self.state.selected_idx].path.resolve() if self.state.tree_entries else self.state.tree_root
        self.rebuild_tree_entries(preferred_path=selected_path)
        self.preview_selected_entry(force=True)
        self.schedule_tree_filter_index_warmup()
        self.mark_tree_watch_dirty()
        self.state.dirty = True

    def toggle_tree_pane(self) -> None:
        """Toggle tree-pane visibility and reflow wrapped source when needed."""
        self.state.browser_visible = not self.state.browser_visible
        if self.state.wrap_text:
            self.rebuild_screen_lines(columns=self.current_term_columns())
        self.state.dirty = True

    def toggle_wrap_mode(self) -> None:
        """Toggle soft-wrap while preserving approximate top source-line context."""
        top_source_line = _source_line_for_display_index(self.state.lines, self.state.start)
        self.state.wrap_text = not self.state.wrap_text
        if self.state.wrap_text:
            self.state.text_x = 0
        self.rebuild_screen_lines(columns=self.current_term_columns())
        self.state.start = _first_display_index_for_source_line(self.state.lines, top_source_line)
        self.state.start = max(0, min(self.state.start, self.state.max_start))
        self.state.dirty = True

    def toggle_help_panel(self) -> None:
        """Toggle help panel visibility and keep selected search hit in view."""
        self.state.show_help = not self.state.show_help
        self.rebuild_screen_lines(columns=self.current_term_columns())
        self._ensure_selected_content_hit_visible()
        self.state.dirty = True

    def _ensure_selected_content_hit_visible(self) -> None:
        """Adjust source scroll so selected content-hit anchor remains visible."""
        if not (
            self.state.tree_filter_active
            and self.state.tree_filter_mode == "content"
            and self.state.tree_filter_query
            and self.state.tree_entries
            and 0 <= self.state.selected_idx < len(self.state.tree_entries)
        ):
            return

        selected_entry = self.state.tree_entries[self.state.selected_idx]
        if selected_entry.kind != "search_hit" or selected_entry.line is None:
            return

        visible_rows = max(1, self.visible_content_rows())
        self.state.max_start = max(0, len(self.state.lines) - visible_rows)
        max_line_index = max(0, len(self.state.lines) - 1)
        anchor = max(0, min(selected_entry.line - 1, max_line_index))
        view_end = self.state.start + visible_rows - 1
        if self.state.start <= anchor <= view_end:
            return

        centered = max(0, anchor - max(1, visible_rows // 3))
        self.state.start = max(0, min(centered, self.state.max_start))

    def execute_command_palette_action(self, command_id: str) -> bool:
        """Execute command-palette action by id, returning ``True`` to quit."""
        if command_id == "filter_files":
            if self.open_tree_filter_fn is not None:
                self.open_tree_filter_fn("files")
            return False
        if command_id == "search_content":
            if self.open_tree_filter_fn is not None:
                self.open_tree_filter_fn("content")
            return False
        if command_id == "open_symbols":
            self.open_symbol_picker()
            return False
        if command_id == "history_back":
            if self.jump_back_in_history():
                self.state.dirty = True
            return False
        if command_id == "history_forward":
            if self.jump_forward_in_history():
                self.state.dirty = True
            return False
        if command_id == "toggle_tree":
            self.toggle_tree_pane()
            return False
        if command_id == "toggle_wrap":
            self.toggle_wrap_mode()
            return False
        if command_id == "toggle_hidden":
            self.toggle_hidden_files()
            return False
        if command_id == "toggle_help":
            self.toggle_help_panel()
            return False
        if command_id == "reroot_selected":
            self.reroot_to_selected_target()
            return False
        if command_id == "reroot_parent":
            self.reroot_to_parent()
            return False
        if command_id == "quit":
            return True
        return False

    def current_jump_location(self) -> JumpLocation:
        """Capture current file path and scroll offsets as a jump location."""
        return JumpLocation(
            path=self.state.current_path.resolve(),
            start=max(0, self.state.start),
            text_x=max(0, self.state.text_x),
        )

    def record_jump_if_changed(self, origin: JumpLocation) -> None:
        """Record ``origin`` in jump history only when position actually changed."""
        normalized_origin = origin.normalized()
        if self.current_jump_location() == normalized_origin:
            return
        self.state.jump_history.record(normalized_origin)

    def apply_jump_location(self, location: JumpLocation) -> bool:
        """Apply jump location and clamp offsets to current rendered content."""
        target = location.normalized()
        current_path = self.state.current_path.resolve()
        path_changed = target.path != current_path
        if path_changed:
            self.jump_to_path(target.path)

        self.state.max_start = max(0, len(self.state.lines) - self.visible_content_rows())
        clamped_start = max(0, min(target.start, self.state.max_start))
        clamped_text_x = 0 if self.state.wrap_text else max(0, target.text_x)
        prev_start = self.state.start
        prev_text_x = self.state.text_x
        self.state.start = clamped_start
        self.state.text_x = clamped_text_x
        return path_changed or self.state.start != prev_start or self.state.text_x != prev_text_x

    def jump_back_in_history(self) -> bool:
        """Jump to previous history location, returning whether state changed."""
        target = self.state.jump_history.go_back(self.current_jump_location())
        if target is None:
            return False
        return self.apply_jump_location(target)

    def jump_forward_in_history(self) -> bool:
        """Jump to next history location, returning whether state changed."""
        target = self.state.jump_history.go_forward(self.current_jump_location())
        if target is None:
            return False
        return self.apply_jump_location(target)

    def set_named_mark(self, mark_key: str) -> bool:
        """Store current jump location under a valid named-mark key."""
        if not is_named_mark_key(mark_key):
            return False
        self.state.named_marks[mark_key] = self.current_jump_location()
        save_named_marks(self.state.named_marks)
        return True

    def jump_to_named_mark(self, mark_key: str) -> bool:
        """Jump to saved named mark and push current location onto history."""
        if not is_named_mark_key(mark_key):
            return False
        target = self.state.named_marks.get(mark_key)
        if target is None:
            return False
        origin = self.current_jump_location()
        if target.normalized() == origin:
            return False
        self.state.jump_history.record(origin)
        return self.apply_jump_location(target)

    def activate_picker_selection(self) -> bool:
        """Activate current picker row for symbols or command palette actions."""
        if self.state.picker_mode == "symbols" and self.state.picker_match_lines:
            selected_line = self.state.picker_match_lines[self.state.picker_selected]
            symbol_file = self.state.picker_symbol_file
            origin = self.current_jump_location()
            self.close_picker()
            if symbol_file is not None and symbol_file.resolve() != self.state.current_path.resolve():
                self.jump_to_path(symbol_file.resolve())
            self.jump_to_line(selected_line)
            self.record_jump_if_changed(origin)
            return False
        if self.state.picker_mode == "commands" and self.state.picker_match_commands:
            command_id = self.state.picker_match_commands[self.state.picker_selected]
            self.close_picker()
            return self.execute_command_palette_action(command_id)
        return False

    def reveal_path_in_tree(self, target: Path) -> None:
        """Expand ancestor directories and rebuild tree focused on ``target``."""
        target = target.resolve()
        if target != self.state.tree_root:
            parent = target.parent
            while True:
                resolved = parent.resolve()
                if resolved == self.state.tree_root:
                    break
                self.state.expanded.add(resolved)
                if resolved.parent == resolved:
                    break
                parent = resolved.parent
        self.state.expanded.add(self.state.tree_root)
        self.rebuild_tree_entries(preferred_path=target, center_selection=True)
        self.mark_tree_watch_dirty()

    def jump_to_path(self, target: Path) -> None:
        """Reveal and open ``target`` path in tree and source preview state."""
        target = target.resolve()
        self.reveal_path_in_tree(target)
        self.state.current_path = target
        self.refresh_rendered_for_current_path()

    def jump_to_line(self, line_number: int) -> None:
        """Scroll source preview near ``line_number`` and reset horizontal offset."""
        visible_rows = max(1, self.visible_content_rows())
        self.state.max_start = max(0, len(self.state.lines) - visible_rows)
        max_line_index = max(0, len(self.state.lines) - 1)
        anchor = max(0, min(line_number, max_line_index))
        centered = max(0, anchor - max(1, visible_rows // 3))
        self.state.start = max(0, min(centered, self.state.max_start))
        self.state.text_x = 0
