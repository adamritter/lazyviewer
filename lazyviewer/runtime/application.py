"""Application object that composes source and tree pane façades."""

from __future__ import annotations

from collections.abc import Callable

from ..input import NormalKeyActions
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
        normal_key_actions: NormalKeyActions,
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
        self.normal_key_actions = normal_key_actions
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
        """Handle one normal-mode key using app-owned state and key ops."""
        return handle_normal_key_event(
            key=key,
            term_columns=term_columns,
            state=self.state,
            actions=self.normal_key_actions,
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
