"""Unit tests for key-handler dispatch behavior.

Verifies mode-aware handling for git/search/help/editor actions.
Keeps keybinding regressions isolated from full runtime integration tests.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lazyviewer.input import (
    NormalKeyContext,
    handle_normal_key,
)
from lazyviewer.runtime.navigation import JumpLocation
from lazyviewer.runtime.state import AppState
from lazyviewer.tree_model import TreeEntry


def _make_state() -> AppState:
    root = Path("/tmp").resolve()
    return AppState(
        current_path=root,
        tree_root=root,
        expanded={root},
        show_hidden=False,
        tree_entries=[TreeEntry(path=root, depth=0, is_dir=True)],
        selected_idx=0,
        rendered="",
        lines=[],
        start=0,
        tree_start=0,
        text_x=0,
        wrap_text=False,
        left_width=24,
        right_width=80,
        usable=24,
        max_start=0,
        last_right_width=80,
    )

class KeyHandlersBehaviorTestsPart3(unittest.TestCase):
    def _invoke(
        self,
        *,
        state: AppState,
        key: str,
        toggle_git_features,
        jump_to_next_git_modified,
        toggle_tree_size_labels=lambda: None,
        open_symbol_picker=lambda: None,
        launch_lazygit=lambda: None,
        launch_editor_for_path=None,
        max_horizontal_text_offset=lambda: 10_000,
        visible_rows: int = 20,
    ) -> bool:
        if launch_editor_for_path is None:
            launch_editor_for_path = lambda _path: None
        context = NormalKeyContext(
            state=state,
            current_jump_location=lambda: JumpLocation(path=state.current_path, start=state.start, text_x=state.text_x),
            record_jump_if_changed=lambda _origin: None,
            open_symbol_picker=open_symbol_picker,
            reroot_to_parent=lambda: None,
            reroot_to_selected_target=lambda: None,
            toggle_hidden_files=lambda: None,
            toggle_tree_pane=lambda: None,
            toggle_wrap_mode=lambda: None,
            toggle_tree_size_labels=toggle_tree_size_labels,
            toggle_help_panel=lambda: None,
            toggle_git_features=toggle_git_features,
            launch_lazygit=launch_lazygit,
            handle_tree_mouse_wheel=lambda _key: False,
            handle_tree_mouse_click=lambda _key: False,
            move_tree_selection=lambda _delta: False,
            rebuild_tree_entries=lambda **_kwargs: None,
            preview_selected_entry=lambda **_kwargs: None,
            refresh_rendered_for_current_path=lambda **_kwargs: None,
            refresh_git_status_overlay=lambda **_kwargs: None,
            maybe_grow_directory_preview=lambda: False,
            max_horizontal_text_offset=max_horizontal_text_offset,
            visible_content_rows=lambda: visible_rows,
            rebuild_screen_lines=lambda **_kwargs: None,
            mark_tree_watch_dirty=lambda: None,
            launch_editor_for_path=launch_editor_for_path,
            jump_to_next_git_modified=jump_to_next_git_modified,
        )
        return handle_normal_key(key, 120, context)

    def test_e_launches_current_directory_when_browser_hidden(self) -> None:
        state = _make_state()
        state.browser_visible = False
        state.current_path = Path("/tmp").resolve()
        launched: list[Path] = []

        should_quit = self._invoke(
            state=state,
            key="e",
            toggle_git_features=lambda: None,
            jump_to_next_git_modified=lambda _direction: False,
            launch_editor_for_path=lambda path: launched.append(path) or None,
        )

        self.assertFalse(should_quit)
        self.assertEqual(launched, [Path("/tmp").resolve()])
        self.assertEqual(state.current_path, Path("/tmp").resolve())

    def test_enter_and_down_move_one_line_the_same(self) -> None:
        state = _make_state()
        state.browser_visible = False

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.py"
            source = (
                "def run():\n"
                "    first = 1\n"
                "    second = 2\n"
                "    third = 3\n"
            )
            path.write_text(source, encoding="utf-8")
            state.current_path = path
            state.lines = source.splitlines(keepends=True)
            state.max_start = len(state.lines) - 1
            should_quit = self._invoke(
                state=state,
                key="ENTER",
                toggle_git_features=lambda: None,
                jump_to_next_git_modified=lambda _direction: False,
                visible_rows=2,
            )
            self.assertFalse(should_quit)
            self.assertEqual(state.start, 1)

            state.start = 0
            should_quit = self._invoke(
                state=state,
                key="DOWN",
                toggle_git_features=lambda: None,
                jump_to_next_git_modified=lambda _direction: False,
                visible_rows=2,
            )

        self.assertFalse(should_quit)
        self.assertEqual(state.start, 1)

    def test_enter_count_keeps_explicit_step_size(self) -> None:
        state = _make_state()
        state.browser_visible = False

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.py"
            source = (
                "def run():\n"
                "    first = 1\n"
                "    second = 2\n"
                "    third = 3\n"
                "    fourth = 4\n"
            )
            path.write_text(source, encoding="utf-8")
            state.current_path = path
            state.lines = source.splitlines(keepends=True)
            state.max_start = len(state.lines) - 1
            state.count_buffer = "3"
            should_quit = self._invoke(
                state=state,
                key="ENTER",
                toggle_git_features=lambda: None,
                jump_to_next_git_modified=lambda _direction: False,
                visible_rows=2,
            )

        self.assertFalse(should_quit)
        self.assertEqual(state.start, 3)

    def test_G_reaches_end_when_sticky_header_reduces_text_rows(self) -> None:
        state = _make_state()
        state.browser_visible = False

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.py"
            source = (
                "def run():\n"
                "    one = 1\n"
                "    two = 2\n"
                "    three = 3\n"
                "    four = 4\n"
            )
            path.write_text(source, encoding="utf-8")
            state.current_path = path
            state.lines = source.splitlines(keepends=True)
            visible_rows = 3
            state.max_start = max(0, len(state.lines) - visible_rows)

            should_quit = self._invoke(
                state=state,
                key="G",
                toggle_git_features=lambda: None,
                jump_to_next_git_modified=lambda _direction: False,
                visible_rows=visible_rows,
            )

        self.assertFalse(should_quit)
        self.assertEqual(state.start, 2)

    def test_space_reaches_end_when_sticky_header_reduces_text_rows(self) -> None:
        state = _make_state()
        state.browser_visible = False

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.py"
            source = (
                "def run():\n"
                "    one = 1\n"
                "    two = 2\n"
                "    three = 3\n"
                "    four = 4\n"
            )
            path.write_text(source, encoding="utf-8")
            state.current_path = path
            state.lines = source.splitlines(keepends=True)
            visible_rows = 3
            state.max_start = max(0, len(state.lines) - visible_rows)

            should_quit = self._invoke(
                state=state,
                key=" ",
                toggle_git_features=lambda: None,
                jump_to_next_git_modified=lambda _direction: False,
                visible_rows=visible_rows,
            )

        self.assertFalse(should_quit)
        self.assertEqual(state.start, 2)

    def test_right_scroll_clamps_to_max_horizontal_offset(self) -> None:
        state = _make_state()
        state.browser_visible = False
        state.wrap_text = False
        state.text_x = 10_000

        should_quit = self._invoke(
            state=state,
            key="RIGHT",
            toggle_git_features=lambda: None,
            jump_to_next_git_modified=lambda _direction: False,
            max_horizontal_text_offset=lambda: 120,
        )

        self.assertFalse(should_quit)
        self.assertEqual(state.text_x, 120)

    def test_e_forces_preview_rebuild_after_successful_edit(self) -> None:
        state = _make_state()
        state.browser_visible = False
        state.current_path = Path("/tmp").resolve()
        refresh_calls: list[dict[str, object]] = []

        context = NormalKeyContext(
            state=state,
            current_jump_location=lambda: JumpLocation(path=state.current_path, start=state.start, text_x=state.text_x),
            record_jump_if_changed=lambda _origin: None,
            open_symbol_picker=lambda: None,
            reroot_to_parent=lambda: None,
            reroot_to_selected_target=lambda: None,
            toggle_hidden_files=lambda: None,
            toggle_tree_pane=lambda: None,
            toggle_wrap_mode=lambda: None,
            toggle_tree_size_labels=lambda: None,
            toggle_help_panel=lambda: None,
            toggle_git_features=lambda: None,
            launch_lazygit=lambda: None,
            handle_tree_mouse_wheel=lambda _key: False,
            handle_tree_mouse_click=lambda _key: False,
            move_tree_selection=lambda _delta: False,
            rebuild_tree_entries=lambda **_kwargs: None,
            preview_selected_entry=lambda **_kwargs: None,
            refresh_rendered_for_current_path=lambda **kwargs: refresh_calls.append(kwargs),
            refresh_git_status_overlay=lambda **_kwargs: None,
            maybe_grow_directory_preview=lambda: False,
            max_horizontal_text_offset=lambda: 10_000,
            visible_content_rows=lambda: 20,
            rebuild_screen_lines=lambda **_kwargs: None,
            mark_tree_watch_dirty=lambda: None,
            launch_editor_for_path=lambda _path: None,
            jump_to_next_git_modified=lambda _direction: False,
        )
        should_quit = handle_normal_key("e", 120, context)

        self.assertFalse(should_quit)
        self.assertEqual(len(refresh_calls), 1)
        self.assertEqual(
            refresh_calls[0],
            {"reset_scroll": True, "reset_dir_budget": True, "force_rebuild": True},
        )
