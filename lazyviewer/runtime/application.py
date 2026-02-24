"""Application object that composes source and tree pane façades."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..input import NormalKeyHandler
from ..source_pane import SourcePane
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
        self._normal_key_handler = NormalKeyHandler(self)

    def handle_tree_mouse_wheel(self, mouse_key: str) -> bool:
        """Forward wheel events to source-pane controller."""
        return self.source_pane.handle_tree_mouse_wheel(mouse_key)

    def handle_tree_mouse_click(self, mouse_key: str) -> bool:
        """Route click events through source pane first, then tree pane."""
        source_result = self.source_pane.handle_tree_mouse_click(mouse_key)
        if source_result.handled:
            return True
        if source_result.route_to_tree:
            return self.tree_pane.handle_tree_mouse_click(mouse_key)
        return False

    def tick_source_selection_drag(self) -> None:
        """Advance active source-selection drag state."""
        self.source_pane.tick_source_selection_drag()

    def handle_normal_key(self, key: str, term_columns: int) -> bool:
        """Handle one normal-mode key by dispatching directly to pane controllers."""
        return self._normal_key_handler.handle(key, term_columns)

    def run(self) -> None:
        """Run the interactive event loop."""
        self._run_main_loop(
            state=self.state,
            terminal=self.terminal,
            stdin_fd=self.stdin_fd,
            timing=self.timing,
            callbacks=self,
        )
