"""Picker and navigation controller with explicit methods (no mixins)."""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from pathlib import Path

from ....runtime.config import save_show_hidden
from ....runtime.state import AppState
from ....search.fuzzy import fuzzy_match_labels
from . import navigation as picker_navigation
from .line_map import first_display_index_for_source_line, source_line_for_display_index
from .panel import PickerPanel

PICKER_RESULT_LIMIT = 200


class NavigationController:
    """State-bound picker/navigation operations used by key handlers."""

    def __init__(
        self,
        *,
        state: AppState,
        command_palette_items: tuple[tuple[str, str], ...],
        rebuild_screen_lines: Callable[..., None],
        rebuild_tree_entries: Callable[..., None],
        preview_selected_entry: Callable[..., None],
        schedule_tree_filter_index_warmup: Callable[[], None],
        mark_tree_watch_dirty: Callable[[], None],
        reset_git_watch_context: Callable[[], None],
        refresh_git_status_overlay: Callable[..., None],
        visible_content_rows: Callable[[], int],
        refresh_rendered_for_current_path: Callable[..., None],
        open_tree_filter: Callable[[str], None],
    ) -> None:
        """Bind controller methods from explicit runtime hooks."""

        self.state = state
        self.command_palette_items = command_palette_items
        self.rebuild_screen_lines = rebuild_screen_lines
        self.rebuild_tree_entries = rebuild_tree_entries
        self.preview_selected_entry = preview_selected_entry
        self.schedule_tree_filter_index_warmup = schedule_tree_filter_index_warmup
        self.mark_tree_watch_dirty = mark_tree_watch_dirty
        self.reset_git_watch_context = reset_git_watch_context
        self.refresh_git_status_overlay = refresh_git_status_overlay
        self.visible_content_rows = visible_content_rows
        self.refresh_rendered_for_current_path = refresh_rendered_for_current_path
        self.open_tree_filter = open_tree_filter
        self.panel = PickerPanel(self)

    # matching
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

    # picker key handling
    def handle_picker_key(self, key: str, double_click_seconds: float) -> tuple[bool, bool]:
        """Handle one key while picker is active."""
        return self.panel.handle_key(key, double_click_seconds)

    # history/navigation
    def current_jump_location(self) -> picker_navigation.JumpLocation:
        """Capture current file path and scroll offsets as a jump location."""
        return picker_navigation.JumpLocation(
            path=self.state.current_path.resolve(),
            start=max(0, self.state.start),
            text_x=max(0, self.state.text_x),
        )

    def record_jump_if_changed(self, origin: picker_navigation.JumpLocation) -> None:
        """Record ``origin`` in jump history only when position actually changed."""
        normalized_origin = origin.normalized()
        if self.current_jump_location() == normalized_origin:
            return
        self.state.jump_history.record(normalized_origin)

    def apply_jump_location(self, location: picker_navigation.JumpLocation) -> bool:
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
        if not picker_navigation.is_named_mark_key(mark_key):
            return False
        self.state.named_marks[mark_key] = self.current_jump_location()
        picker_navigation.save_named_marks(self.state.named_marks)
        return True

    def jump_to_named_mark(self, mark_key: str) -> bool:
        """Jump to saved named mark and push current location onto history."""
        if not picker_navigation.is_named_mark_key(mark_key):
            return False
        target = self.state.named_marks.get(mark_key)
        if target is None:
            return False
        origin = self.current_jump_location()
        if target.normalized() == origin:
            return False
        self.state.jump_history.record(origin)
        return self.apply_jump_location(target)

    def _normalize_tree_roots(self, *, include_active: bool = True) -> list[Path]:
        """Normalize roots and synchronize per-root expansion state."""
        normalized: list[Path] = []
        seen: set[Path] = set()
        for raw_root in self.state.tree_roots:
            resolved = raw_root.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            normalized.append(resolved)

        active_root = self.state.tree_root.resolve()
        if include_active and active_root not in seen:
            normalized.append(active_root)

        self.state.tree_roots = normalized
        workspace_expanded: dict[Path, set[Path]] = {}
        flat_union: set[Path] = set()
        for root in normalized:
            existing = self.state.workspace_expanded.get(root)
            if existing is not None:
                scoped = {
                    candidate.resolve()
                    for candidate in existing
                    if candidate.resolve().is_relative_to(root)
                }
            else:
                scoped = {
                    candidate.resolve()
                    for candidate in self.state.expanded
                    if candidate.resolve().is_relative_to(root)
                }
            workspace_expanded[root] = scoped
            flat_union.update(scoped)
        self.state.workspace_expanded = workspace_expanded
        self.state.expanded = flat_union
        return normalized

    def _switch_active_tree_root(
        self,
        target_root: Path,
        *,
        preferred_path: Path | None,
        preserve_old_root_expanded: bool = False,
        include_previous_root: bool = True,
    ) -> None:
        """Switch active root, rebuild view, and resync watch/git state."""
        old_root = self.state.tree_root.resolve()
        target_root = target_root.resolve()
        roots = self._normalize_tree_roots(include_active=include_previous_root)
        if target_root not in roots:
            roots.append(target_root)
        self.state.tree_roots = roots

        self.state.tree_root = target_root
        if preserve_old_root_expanded and old_root != target_root:
            self.state.expanded = {target_root, old_root}
        else:
            self.state.expanded = {target_root}
        self.state.workspace_expanded = {}
        self._normalize_tree_roots(include_active=include_previous_root)

        focused_path = target_root if preferred_path is None else preferred_path.resolve()
        if not focused_path.is_relative_to(target_root):
            focused_path = target_root
        self.rebuild_tree_entries(preferred_path=focused_path, center_selection=True)
        self.preview_selected_entry(force=True)
        self.schedule_tree_filter_index_warmup()
        self.mark_tree_watch_dirty()
        self.reset_git_watch_context()
        self.refresh_git_status_overlay(force=True)
        self.state.dirty = True

    def _selected_target_and_root(self) -> tuple[Path, Path]:
        """Return selected target path and its directory root candidate."""
        selected_entry = (
            self.state.tree_entries[self.state.selected_idx]
            if self.state.tree_entries and 0 <= self.state.selected_idx < len(self.state.tree_entries)
            else None
        )
        if selected_entry is not None:
            selected_target = selected_entry.path.resolve()
            target_root = selected_target if selected_entry.is_dir else selected_target.parent.resolve()
            return selected_target, target_root

        selected_target = self.state.current_path.resolve()
        target_root = selected_target if selected_target.is_dir() else selected_target.parent.resolve()
        return selected_target, target_root

    def reveal_path_in_tree(self, target: Path) -> None:
        """Expand ancestor directories and rebuild tree focused on ``target``."""
        target = target.resolve()
        roots = self._normalize_tree_roots()
        scope_root = next((root for root in roots if target.is_relative_to(root)), self.state.tree_root.resolve())
        scoped = set(self.state.workspace_expanded.get(scope_root, {scope_root}))
        scoped.add(scope_root)
        if target != scope_root:
            parent = target.parent
            while True:
                resolved = parent.resolve()
                if resolved == scope_root:
                    break
                scoped.add(resolved)
                if resolved.parent == resolved:
                    break
                parent = resolved.parent
        self.state.workspace_expanded[scope_root] = scoped
        self.state.expanded = set().union(*self.state.workspace_expanded.values())
        self.rebuild_tree_entries(preferred_path=target, center_selection=True)
        self.mark_tree_watch_dirty()

    def jump_to_path(self, target: Path) -> None:
        """Reveal and open ``target`` path in tree and source preview state."""
        target = target.resolve()
        current_root = self.state.tree_root.resolve()
        if not target.is_relative_to(current_root):
            workspace_root = next(
                (root for root in self._normalize_tree_roots() if target.is_relative_to(root)),
                None,
            )
            if workspace_root is not None:
                self._switch_active_tree_root(
                    workspace_root,
                    preferred_path=target,
                )
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

    # view actions
    def current_term_columns(self) -> int:
        """Return current terminal width used for render rebuild calls."""
        return shutil.get_terminal_size((80, 24)).columns

    def reroot_to_parent(self) -> None:
        """Move tree root to parent directory, preserving previous root expanded."""
        old_root = self.state.tree_root.resolve()
        parent_root = old_root.parent.resolve()
        if parent_root == old_root:
            return
        self._switch_active_tree_root(
            parent_root,
            preferred_path=old_root,
            preserve_old_root_expanded=True,
        )

    def reroot_to_selected_target(self) -> None:
        """Reroot tree at selected entry (or current path fallback) directory."""
        selected_target, target_root = self._selected_target_and_root()
        old_root = self.state.tree_root.resolve()
        if target_root == old_root:
            return
        self._switch_active_tree_root(
            target_root,
            preferred_path=selected_target,
        )

    def add_workspace_root_from_selected_target(self) -> None:
        """Add selected directory as a workspace root without changing root priority."""
        selected_target, target_root = self._selected_target_and_root()
        roots = self._normalize_tree_roots()
        if target_root not in roots:
            roots.append(target_root)
        self.state.tree_roots = roots
        self.state.expanded.add(target_root)
        self._normalize_tree_roots()
        scoped = set(self.state.workspace_expanded.get(target_root, set()))
        scoped.add(target_root)
        self.state.workspace_expanded[target_root] = scoped
        self.state.expanded = set().union(*self.state.workspace_expanded.values())
        self.rebuild_tree_entries(
            preferred_path=target_root,
            preferred_workspace_root=target_root,
            center_selection=True,
        )
        self.preview_selected_entry(force=True)
        self.schedule_tree_filter_index_warmup()
        self.mark_tree_watch_dirty()
        self.reset_git_watch_context()
        self.refresh_git_status_overlay(force=True)
        self.state.dirty = True

    def remove_active_workspace_root(self) -> None:
        """Remove selected workspace root (or current-file root) when possible."""
        roots = self._normalize_tree_roots()
        if len(roots) <= 1:
            self.state.status_message = "cannot delete the only root"
            self.state.status_message_until = time.monotonic() + 1.5
            self.state.dirty = True
            return

        selected_root: Path | None = None
        if self.state.tree_entries and 0 <= self.state.selected_idx < len(self.state.tree_entries):
            selected_entry = self.state.tree_entries[self.state.selected_idx]
            if selected_entry.is_dir and selected_entry.depth == 0:
                candidate = selected_entry.path.resolve()
                if candidate in roots:
                    selected_root = candidate

        if selected_root is None:
            current_path = self.state.current_path.resolve()
            selected_root = next(
                (
                    root
                    for root in sorted(roots, key=lambda item: len(item.parts), reverse=True)
                    if current_path.is_relative_to(root)
                ),
                None,
            )

        if selected_root is None:
            selected_root = self.state.tree_root.resolve() if self.state.tree_root.resolve() in roots else roots[-1]

        roots = [root for root in roots if root != selected_root]
        if not roots:
            return
        self.state.tree_roots = roots
        self.state.expanded.discard(selected_root)
        self.state.workspace_expanded.pop(selected_root, None)
        self._normalize_tree_roots()

        if self.state.tree_root.resolve() == selected_root:
            self.state.tree_root = roots[0]

        preferred_path = self.state.current_path.resolve()
        if not any(preferred_path.is_relative_to(root) for root in roots):
            preferred_path = roots[0]
            self.state.current_path = preferred_path
        self.rebuild_tree_entries(preferred_path=preferred_path, center_selection=True)
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
        top_source_line = source_line_for_display_index(self.state.lines, self.state.start)
        self.state.wrap_text = not self.state.wrap_text
        if self.state.wrap_text:
            self.state.text_x = 0
        self.rebuild_screen_lines(columns=self.current_term_columns())
        self.state.start = first_display_index_for_source_line(self.state.lines, top_source_line)
        self.state.start = max(0, min(self.state.start, self.state.max_start))
        self.state.dirty = True

    def toggle_help_panel(self) -> None:
        """Toggle help panel visibility and keep selected search hit in view."""
        self.state.show_help = not self.state.show_help
        self.rebuild_screen_lines(columns=self.current_term_columns())
        self.ensure_selected_content_hit_visible()
        self.state.dirty = True

    def ensure_selected_content_hit_visible(self) -> None:
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
            self.open_tree_filter("files")
            return False
        if command_id == "search_content":
            self.open_tree_filter("content")
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

    # picker lifecycle
    def resolve_symbol_target(self) -> Path | None:
        """Resolve file path whose symbols should populate the symbol picker."""
        return self.panel.resolve_symbol_target()

    def open_symbol_picker(self) -> None:
        """Enter symbol-picker mode and populate symbols for current file target."""
        self.panel.open_symbol_picker()

    def open_command_picker(self) -> None:
        """Enter command-palette mode and load command label/id lists."""
        self.panel.open_command_picker()

    def close_picker(self, reset_query: bool = True) -> None:
        """Close picker UI and restore non-picker browser visibility state."""
        self.panel.close_picker(reset_query=reset_query)

    def activate_picker_selection(self) -> bool:
        """Activate current picker row for symbols or command palette actions."""
        return self.panel.activate_picker_selection()
