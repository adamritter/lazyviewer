"""Application object that composes source and tree pane façades."""

from __future__ import annotations

from collections.abc import Callable

from ..input import NormalKeyOps
from ..input import handle_normal_key as handle_normal_key_event
from ..source_pane.pane import SourcePane
from ..tree_pane.pane import TreePane
from .layout import PagerLayoutOps
from .loop import RuntimeLoopCallbacks, RuntimeLoopTiming
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
        layout: PagerLayoutOps,
        source_pane: SourcePane,
        tree_pane: TreePane,
        maybe_refresh_tree_watch: Callable[[], None],
        maybe_refresh_git_watch: Callable[[], None],
        refresh_git_status_overlay: Callable[..., None],
        normal_key_ops: NormalKeyOps,
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
        self.normal_key_ops = normal_key_ops
        self.save_left_pane_width = save_left_pane_width
        self._run_main_loop = run_main_loop_fn
        self._loop_callbacks = RuntimeLoopCallbacks(
            get_tree_filter_loading_until=self.tree_pane.filter.get_loading_until,
            tree_view_rows=self.tree_pane.filter.tree_view_rows,
            tree_filter_prompt_prefix=self.tree_pane.filter.tree_filter_prompt_prefix,
            tree_filter_placeholder=self.tree_pane.filter.tree_filter_placeholder,
            visible_content_rows=self.source_pane.visible_content_rows,
            rebuild_screen_lines=self.layout.rebuild_screen_lines,
            maybe_refresh_tree_watch=self.maybe_refresh_tree_watch,
            maybe_refresh_git_watch=self.maybe_refresh_git_watch,
            refresh_git_status_overlay=self.refresh_git_status_overlay,
            current_preview_image_path=self.layout.current_preview_image_path,
            current_preview_image_geometry=self.layout.current_preview_image_geometry,
            open_tree_filter=self.tree_pane.filter.open_tree_filter,
            open_command_picker=self.tree_pane.navigation.open_command_picker,
            close_picker=self.tree_pane.navigation.close_picker,
            refresh_command_picker_matches=self.tree_pane.navigation.refresh_command_picker_matches,
            activate_picker_selection=self.tree_pane.navigation.activate_picker_selection,
            refresh_active_picker_matches=self.tree_pane.navigation.refresh_active_picker_matches,
            handle_tree_mouse_wheel=self.source_pane.handle_tree_mouse_wheel,
            handle_tree_mouse_click=self.tree_pane.handle_tree_mouse_click,
            toggle_help_panel=self.tree_pane.navigation.toggle_help_panel,
            close_tree_filter=self.tree_pane.filter.close_tree_filter,
            activate_tree_filter_selection=self.tree_pane.filter.activate_tree_filter_selection,
            move_tree_selection=self.tree_pane.filter.move_tree_selection,
            apply_tree_filter_query=self.tree_pane.filter.apply_tree_filter_query,
            jump_to_next_content_hit=self.tree_pane.filter.jump_to_next_content_hit,
            set_named_mark=self.tree_pane.navigation.set_named_mark,
            jump_to_named_mark=self.tree_pane.navigation.jump_to_named_mark,
            jump_back_in_history=self.tree_pane.navigation.jump_back_in_history,
            jump_forward_in_history=self.tree_pane.navigation.jump_forward_in_history,
            handle_normal_key=self.handle_normal_key,
            save_left_pane_width=self.save_left_pane_width,
            tick_source_selection_drag=self.tree_pane.tick_source_selection_drag,
        )

    def handle_normal_key(self, key: str, term_columns: int) -> bool:
        """Handle one normal-mode key using app-owned state and key ops."""
        return handle_normal_key_event(
            key=key,
            term_columns=term_columns,
            state=self.state,
            ops=self.normal_key_ops,
        )

    def run(self) -> None:
        """Run the interactive event loop."""
        self._run_main_loop(
            state=self.state,
            terminal=self.terminal,
            stdin_fd=self.stdin_fd,
            timing=self.timing,
            callbacks=self._loop_callbacks,
        )
