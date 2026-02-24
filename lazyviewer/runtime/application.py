"""Application object that composes source and tree pane façades."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..input import handle_normal_key as handle_normal_key_event
from ..source_pane.pane import SourcePane
from ..tree_pane.pane import TreePane
from .layout import PagerLayout
from .loop import RuntimeLoopTiming
from .state import AppState
from .terminal import TerminalController


class App:
    """Composed runtime app owning pane controllers and loop wiring."""

    def __init__(
        self,
        *,
        state: AppState,
        terminal: TerminalController,
        stdin_fd: int,
        timing: RuntimeLoopTiming,
        layout: PagerLayout,
        source_pane: SourcePane,
        tree_pane: TreePane,
        maybe_refresh_tree_watch: Callable[[], None],
        maybe_refresh_git_watch: Callable[[], None],
        refresh_git_status_overlay: Callable[..., None],
        toggle_tree_size_labels: Callable[[], None],
        toggle_git_features: Callable[[], None],
        launch_lazygit: Callable[[], None],
        mark_tree_watch_dirty: Callable[[], None],
        preview_selected_entry: Callable[..., None],
        refresh_rendered_for_current_path: Callable[..., None],
        maybe_grow_directory_preview: Callable[[], bool],
        launch_editor_for_path: Callable[[Path], str | None],
        jump_to_next_git_modified: Callable[[int], bool],
        save_left_pane_width: Callable[[int, int], None],
        run_main_loop_fn: Callable[..., None],
    ) -> None:
        self.state = state
        self.terminal = terminal
        self.stdin_fd = stdin_fd
        self.timing = timing
        self.layout = layout
        self.source_pane = source_pane
        self.tree_pane = tree_pane
        self.maybe_refresh_tree_watch = maybe_refresh_tree_watch
        self.maybe_refresh_git_watch = maybe_refresh_git_watch
        self.refresh_git_status_overlay = refresh_git_status_overlay
        self.toggle_tree_size_labels = toggle_tree_size_labels
        self.toggle_git_features = toggle_git_features
        self.launch_lazygit = launch_lazygit
        self.mark_tree_watch_dirty = mark_tree_watch_dirty
        self.preview_selected_entry = preview_selected_entry
        self.refresh_rendered_for_current_path = refresh_rendered_for_current_path
        self.maybe_grow_directory_preview = maybe_grow_directory_preview
        self.launch_editor_for_path = launch_editor_for_path
        self.jump_to_next_git_modified = jump_to_next_git_modified
        self.save_left_pane_width = save_left_pane_width
        self._run_main_loop = run_main_loop_fn
        # Compatibility callback surface used by integration tests that mock run_main_loop
        # and inspect callbacks on this app object directly.
        self.get_tree_filter_loading_until = self.tree_pane.filter.get_loading_until
        self.tree_view_rows = self.tree_pane.filter.tree_view_rows
        self.tree_filter_prompt_prefix = self.tree_pane.filter.tree_filter_prompt_prefix
        self.tree_filter_placeholder = self.tree_pane.filter.tree_filter_placeholder
        self.visible_content_rows = self.source_pane.geometry.visible_content_rows
        self.rebuild_screen_lines = self.layout.rebuild_screen_lines
        self.current_preview_image_path = self.layout.current_preview_image_path
        self.current_preview_image_geometry = self.layout.current_preview_image_geometry
        self.open_tree_filter = self.tree_pane.filter.open_tree_filter
        self.open_command_picker = self.tree_pane.navigation.open_command_picker
        self.close_picker = self.tree_pane.navigation.close_picker
        self.refresh_command_picker_matches = self.tree_pane.navigation.refresh_command_picker_matches
        self.activate_picker_selection = self.tree_pane.navigation.activate_picker_selection
        self.refresh_active_picker_matches = self.tree_pane.navigation.refresh_active_picker_matches
        self.handle_tree_mouse_wheel = self.source_pane.handle_tree_mouse_wheel
        self.handle_tree_mouse_click = self.tree_pane.handle_tree_mouse_click
        self.toggle_help_panel = self.tree_pane.navigation.toggle_help_panel
        self.close_tree_filter = self.tree_pane.filter.close_tree_filter
        self.activate_tree_filter_selection = self.tree_pane.filter.activate_tree_filter_selection
        self.move_tree_selection = self.tree_pane.filter.move_tree_selection
        self.apply_tree_filter_query = self.tree_pane.filter.apply_tree_filter_query
        self.jump_to_next_content_hit = self.tree_pane.filter.jump_to_next_content_hit
        self.set_named_mark = self.tree_pane.navigation.set_named_mark
        self.jump_to_named_mark = self.tree_pane.navigation.jump_to_named_mark
        self.jump_back_in_history = self.tree_pane.navigation.jump_back_in_history
        self.jump_forward_in_history = self.tree_pane.navigation.jump_forward_in_history
        self.tick_source_selection_drag = self.tree_pane.tick_source_selection_drag

    def handle_normal_key(self, key: str, term_columns: int) -> bool:
        """Handle one normal-mode key by dispatching directly to pane controllers."""
        return handle_normal_key_event(
            key=key,
            term_columns=term_columns,
            state=self.state,
            current_jump_location=self.tree_pane.navigation.current_jump_location,
            record_jump_if_changed=self.tree_pane.navigation.record_jump_if_changed,
            open_symbol_picker=self.tree_pane.navigation.open_symbol_picker,
            reroot_to_parent=self.tree_pane.navigation.reroot_to_parent,
            reroot_to_selected_target=self.tree_pane.navigation.reroot_to_selected_target,
            toggle_hidden_files=self.tree_pane.navigation.toggle_hidden_files,
            toggle_tree_pane=self.tree_pane.navigation.toggle_tree_pane,
            toggle_wrap_mode=self.tree_pane.navigation.toggle_wrap_mode,
            toggle_tree_size_labels=self.toggle_tree_size_labels,
            toggle_help_panel=self.tree_pane.navigation.toggle_help_panel,
            toggle_git_features=self.toggle_git_features,
            launch_lazygit=self.launch_lazygit,
            handle_tree_mouse_wheel=self.source_pane.handle_tree_mouse_wheel,
            handle_tree_mouse_click=self.tree_pane.handle_tree_mouse_click,
            move_tree_selection=self.tree_pane.filter.move_tree_selection,
            rebuild_tree_entries=self.tree_pane.filter.rebuild_tree_entries,
            preview_selected_entry=self.preview_selected_entry,
            refresh_rendered_for_current_path=self.refresh_rendered_for_current_path,
            refresh_git_status_overlay=self.refresh_git_status_overlay,
            maybe_grow_directory_preview=self.maybe_grow_directory_preview,
            visible_content_rows=self.source_pane.geometry.visible_content_rows,
            rebuild_screen_lines=self.layout.rebuild_screen_lines,
            mark_tree_watch_dirty=self.mark_tree_watch_dirty,
            launch_editor_for_path=self.launch_editor_for_path,
            jump_to_next_git_modified=self.jump_to_next_git_modified,
            max_horizontal_text_offset=self.source_pane.geometry.max_horizontal_text_offset,
        )

    def run(self) -> None:
        """Run the interactive event loop."""
        self._run_main_loop(
            state=self.state,
            terminal=self.terminal,
            stdin_fd=self.stdin_fd,
            timing=self.timing,
            callbacks=self,
        )
