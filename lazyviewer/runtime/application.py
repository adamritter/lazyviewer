"""Application root composed from cohesive component and operation bundles."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..input import NormalKeyContext, NormalKeyHandler
from ..render import RetainedPageRenderer
from ..session import SessionState
from ..source_pane import SourcePane
from ..tree_pane.pane import TreePane
from .layout import PagerLayout
from .loop import RuntimeLoopTiming
from .terminal import TerminalController


class RefreshGitStatus(Protocol):
    def __call__(self, force: bool = False) -> None: ...


class RefreshPreview(Protocol):
    def __call__(
        self,
        *,
        reset_scroll: bool = True,
        reset_dir_budget: bool = False,
        force_rebuild: bool = False,
    ) -> None: ...


class RunMainLoop(Protocol):
    def __call__(
        self,
        *,
        state: SessionState,
        terminal: TerminalController,
        stdin_fd: int,
        timing: RuntimeLoopTiming,
        callbacks: object,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class ApplicationComponents:
    state: SessionState
    terminal: TerminalController
    layout: PagerLayout
    source_pane: SourcePane
    tree_pane: TreePane


@dataclass(frozen=True, slots=True)
class ApplicationOperations:
    maybe_refresh_tree_watch: Callable[[], None]
    maybe_refresh_git_watch: Callable[[], None]
    refresh_git_status: RefreshGitStatus
    toggle_tree_size_labels: Callable[[], None]
    toggle_git_features: Callable[[], None]
    launch_lazygit: Callable[[], None]
    mark_tree_watch_dirty: Callable[[], None]
    preview_selected_entry: Callable[[], None]
    refresh_preview: RefreshPreview
    maybe_grow_directory_preview: Callable[[], bool]
    poll_preview_results: Callable[[], bool]
    prefetch_directory_preview: Callable[[], bool]
    launch_editor_for_path: Callable[[Path], str | None]
    jump_to_next_git_modified: Callable[[int], bool]
    save_left_pane_width: Callable[[int, int], None]
    run_main_loop: RunMainLoop


class App:
    """UI root exposing the loop contract over cohesive bundles."""

    def __init__(
        self,
        *,
        components: ApplicationComponents,
        operations: ApplicationOperations,
        stdin_fd: int,
        timing: RuntimeLoopTiming,
    ) -> None:
        self.state = components.state
        self.terminal = components.terminal
        self.layout = components.layout
        self.source_pane = components.source_pane
        self.tree_pane = components.tree_pane
        self.stdin_fd = stdin_fd
        self.timing = timing
        self._operations = operations
        self._page_renderer = RetainedPageRenderer(canonical_text=False)
        self.render_frame = self._page_renderer.render

        # Explicit loop ports; no dynamic adapter or reflection is involved.
        self.maybe_refresh_tree_watch = operations.maybe_refresh_tree_watch
        self.maybe_refresh_git_watch = operations.maybe_refresh_git_watch
        self.refresh_git_status_overlay = operations.refresh_git_status
        self.maybe_poll_directory_preview_results = operations.poll_preview_results
        self.maybe_prefetch_directory_preview = operations.prefetch_directory_preview
        self.save_left_pane_width = operations.save_left_pane_width

        navigation = self.tree_pane.navigation
        self._normal_key_handler = NormalKeyHandler(
            NormalKeyContext(
                state=self.state,
                current_jump_location=navigation.current_jump_location,
                record_jump_if_changed=navigation.record_jump_if_changed,
                open_symbol_picker=self.tree_pane.picker_panel.open_symbol_picker,
                reroot_to_parent=navigation.reroot_to_parent,
                reroot_to_selected_target=navigation.reroot_to_selected_target,
                add_workspace_root_from_selected_target=(
                    navigation.add_workspace_root_from_selected_target
                ),
                remove_active_workspace_root=navigation.remove_active_workspace_root,
                toggle_hidden_files=navigation.toggle_hidden_files,
                toggle_tree_pane=navigation.toggle_tree_pane,
                toggle_wrap_mode=navigation.toggle_wrap_mode,
                toggle_tree_size_labels=operations.toggle_tree_size_labels,
                toggle_help_panel=navigation.toggle_help_panel,
                toggle_git_features=operations.toggle_git_features,
                launch_lazygit=operations.launch_lazygit,
                handle_tree_mouse_wheel=self.handle_tree_mouse_wheel,
                handle_tree_mouse_click=self.handle_tree_mouse_click,
                move_tree_selection=self.tree_pane.filter.move_tree_selection,
                rebuild_tree_entries=self.tree_pane.filter.rebuild_tree_entries,
                preview_selected_entry=operations.preview_selected_entry,
                refresh_rendered_for_current_path=operations.refresh_preview,
                refresh_git_status_overlay=operations.refresh_git_status,
                maybe_grow_directory_preview=operations.maybe_grow_directory_preview,
                visible_content_rows=self.source_pane.geometry.visible_content_rows,
                max_horizontal_text_offset=self.source_pane.geometry.max_horizontal_text_offset,
                rebuild_screen_lines=self.layout.rebuild_screen_lines,
                mark_tree_watch_dirty=operations.mark_tree_watch_dirty,
                launch_editor_for_path=operations.launch_editor_for_path,
                jump_to_next_git_modified=operations.jump_to_next_git_modified,
            )
        )

    def handle_tree_mouse_wheel(self, mouse_key: str) -> bool:
        return self.source_pane.handle_tree_mouse_wheel(mouse_key)

    def handle_tree_mouse_click(self, mouse_key: str) -> bool:
        origin = self.tree_pane.navigation.current_jump_location()
        source_result = self.source_pane.handle_tree_mouse_click(mouse_key)
        if source_result.handled:
            self.tree_pane.navigation.record_jump_if_changed(origin)
            return True
        if source_result.route_to_tree:
            handled = self.tree_pane.handle_tree_mouse_click(mouse_key)
            if handled:
                self.tree_pane.navigation.record_jump_if_changed(origin)
            return handled
        return False

    def tick_source_selection_drag(self) -> None:
        self.source_pane.tick_source_selection_drag()

    def tick_tree_filter_search(self, timeout: float) -> bool:
        return self.tree_pane.filter.poll_content_search_updates(timeout)

    def handle_picker_key(self, key: str, seconds: float) -> tuple[bool, bool]:
        return self.tree_pane.picker_panel.handle_key(key, seconds)

    def handle_tree_filter_key(self, key: str) -> bool:
        return self.tree_pane.filter_panel.handle_key(
            key,
            handle_tree_mouse_wheel=self.handle_tree_mouse_wheel,
            handle_tree_mouse_click=self.handle_tree_mouse_click,
            toggle_help_panel=self.tree_pane.navigation.toggle_help_panel,
        )

    def handle_normal_key(self, key: str, term_columns: int) -> bool:
        return self._normal_key_handler.handle(key, term_columns)

    def run(self) -> None:
        self._operations.run_main_loop(
            state=self.state,
            terminal=self.terminal,
            stdin_fd=self.stdin_fd,
            timing=self.timing,
            callbacks=self,
        )


__all__ = ["App", "ApplicationComponents", "ApplicationOperations"]
