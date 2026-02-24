"""Application object that composes source and tree pane façades."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..input import NormalKeyOps
from ..input import handle_normal_key as handle_normal_key_event
from ..source_pane.pane import SourcePane
from ..tree_pane.pane import TreePane
from .layout import PagerLayoutOps
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

    # loop callback surface
    def get_tree_filter_loading_until(self) -> float:
        return self.tree_pane.filter.get_loading_until()

    def tree_view_rows(self) -> int:
        return self.tree_pane.filter.tree_view_rows()

    def tree_filter_prompt_prefix(self) -> str:
        return self.tree_pane.filter.tree_filter_prompt_prefix()

    def tree_filter_placeholder(self) -> str:
        return self.tree_pane.filter.tree_filter_placeholder()

    def visible_content_rows(self) -> int:
        return self.source_pane.visible_content_rows()

    def rebuild_screen_lines(self, *args, **kwargs) -> None:
        self.layout.rebuild_screen_lines(*args, **kwargs)

    def current_preview_image_path(self) -> Path | None:
        return self.layout.current_preview_image_path()

    def current_preview_image_geometry(self, columns: int) -> tuple[int, int, int, int]:
        return self.layout.current_preview_image_geometry(columns)

    def open_tree_filter(self, mode: str) -> None:
        self.tree_pane.filter.open_tree_filter(mode)

    def open_command_picker(self) -> None:
        self.tree_pane.navigation.open_command_picker()

    def close_picker(self, *args, **kwargs) -> None:
        self.tree_pane.navigation.close_picker(*args, **kwargs)

    def refresh_command_picker_matches(self, *args, **kwargs) -> None:
        self.tree_pane.navigation.refresh_command_picker_matches(*args, **kwargs)

    def activate_picker_selection(self) -> bool:
        return self.tree_pane.navigation.activate_picker_selection()

    def refresh_active_picker_matches(self, *args, **kwargs) -> None:
        self.tree_pane.navigation.refresh_active_picker_matches(*args, **kwargs)

    def handle_tree_mouse_wheel(self, mouse_key: str) -> bool:
        return self.source_pane.handle_tree_mouse_wheel(mouse_key)

    def handle_tree_mouse_click(self, mouse_key: str) -> bool:
        return self.tree_pane.handle_tree_mouse_click(mouse_key)

    def toggle_help_panel(self) -> None:
        self.tree_pane.navigation.toggle_help_panel()

    def close_tree_filter(self, *args, **kwargs) -> None:
        self.tree_pane.filter.close_tree_filter(*args, **kwargs)

    def activate_tree_filter_selection(self) -> None:
        self.tree_pane.filter.activate_tree_filter_selection()

    def move_tree_selection(self, direction: int) -> bool:
        return self.tree_pane.filter.move_tree_selection(direction)

    def apply_tree_filter_query(self, *args, **kwargs) -> None:
        self.tree_pane.filter.apply_tree_filter_query(*args, **kwargs)

    def jump_to_next_content_hit(self, direction: int) -> bool:
        return self.tree_pane.filter.jump_to_next_content_hit(direction)

    def set_named_mark(self, key: str) -> bool:
        return self.tree_pane.navigation.set_named_mark(key)

    def jump_to_named_mark(self, key: str) -> bool:
        return self.tree_pane.navigation.jump_to_named_mark(key)

    def jump_back_in_history(self) -> bool:
        return self.tree_pane.navigation.jump_back_in_history()

    def jump_forward_in_history(self) -> bool:
        return self.tree_pane.navigation.jump_forward_in_history()

    def tick_source_selection_drag(self) -> None:
        self.tree_pane.tick_source_selection_drag()

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
            callbacks=self,
        )
