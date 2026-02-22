"""Unit tests for key-handler dispatch behavior.

Verifies mode-aware handling for git/search/help/editor actions.
Keeps keybinding regressions isolated from full runtime integration tests.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lazyviewer.key_handlers import handle_normal_key, handle_picker_key, handle_tree_filter_key
from lazyviewer.navigation import JumpLocation
from lazyviewer.state import AppState
from lazyviewer.tree import TreeEntry


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


class KeyHandlersBehaviorTests(unittest.TestCase):
    def _invoke(
        self,
        *,
        state: AppState,
        key: str,
        toggle_git_features,
        jump_to_next_git_modified,
        launch_lazygit=lambda: None,
        launch_editor_for_path=None,
        visible_rows: int = 20,
    ) -> bool:
        if launch_editor_for_path is None:
            launch_editor_for_path = lambda _path: None
        return handle_normal_key(
            key=key,
            term_columns=120,
            state=state,
            current_jump_location=lambda: JumpLocation(path=state.current_path, start=state.start, text_x=state.text_x),
            record_jump_if_changed=lambda _origin: None,
            open_symbol_picker=lambda: None,
            reroot_to_parent=lambda: None,
            reroot_to_selected_target=lambda: None,
            toggle_hidden_files=lambda: None,
            toggle_tree_pane=lambda: None,
            toggle_wrap_mode=lambda: None,
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
            visible_content_rows=lambda: visible_rows,
            rebuild_screen_lines=lambda **_kwargs: None,
            mark_tree_watch_dirty=lambda: None,
            launch_editor_for_path=launch_editor_for_path,
            jump_to_next_git_modified=jump_to_next_git_modified,
        )

    def test_ctrl_g_launches_lazygit(self) -> None:
        state = _make_state()
        called = {"count": 0}

        should_quit = self._invoke(
            state=state,
            key="CTRL_G",
            toggle_git_features=lambda: None,
            launch_lazygit=lambda: called.__setitem__("count", called["count"] + 1),
            jump_to_next_git_modified=lambda _direction: False,
        )

        self.assertFalse(should_quit)
        self.assertEqual(called["count"], 1)

    def test_ctrl_o_toggles_git_features(self) -> None:
        state = _make_state()
        called = {"count": 0}

        should_quit = self._invoke(
            state=state,
            key="CTRL_O",
            toggle_git_features=lambda: called.__setitem__("count", called["count"] + 1),
            launch_lazygit=lambda: None,
            jump_to_next_git_modified=lambda _direction: False,
        )

        self.assertFalse(should_quit)
        self.assertEqual(called["count"], 1)

    def test_ctrl_c_quits_in_normal_mode(self) -> None:
        state = _make_state()

        should_quit = self._invoke(
            state=state,
            key="\x03",
            toggle_git_features=lambda: None,
            launch_lazygit=lambda: None,
            jump_to_next_git_modified=lambda _direction: False,
        )

        self.assertTrue(should_quit)

    def test_ctrl_c_closes_picker(self) -> None:
        state = _make_state()
        state.picker_active = True
        state.picker_mode = "commands"
        state.picker_query = "abc"
        close_calls = {"count": 0}

        handled, should_quit = handle_picker_key(
            key="\x03",
            state=state,
            double_click_seconds=0.25,
            close_picker=lambda: close_calls.__setitem__("count", close_calls["count"] + 1),
            refresh_command_picker_matches=lambda **_kwargs: None,
            activate_picker_selection=lambda: False,
            visible_content_rows=lambda: 20,
            refresh_active_picker_matches=lambda **_kwargs: None,
        )

        self.assertTrue(handled)
        self.assertFalse(should_quit)
        self.assertEqual(close_calls["count"], 1)

    def test_n_is_ignored_when_git_features_disabled(self) -> None:
        state = _make_state()
        state.git_features_enabled = False
        called = {"count": 0}

        should_quit = self._invoke(
            state=state,
            key="n",
            toggle_git_features=lambda: None,
            jump_to_next_git_modified=lambda _direction: called.__setitem__("count", called["count"] + 1) or True,
        )

        self.assertFalse(should_quit)
        self.assertEqual(called["count"], 0)

    def test_n_jumps_to_git_modified_when_enabled(self) -> None:
        state = _make_state()
        state.git_features_enabled = True
        called = {"count": 0}

        should_quit = self._invoke(
            state=state,
            key="n",
            toggle_git_features=lambda: None,
            jump_to_next_git_modified=lambda _direction: called.__setitem__("count", called["count"] + 1) or True,
        )

        self.assertFalse(should_quit)
        self.assertEqual(called["count"], 1)

    def test_question_character_is_appended_while_tree_filter_editing(self) -> None:
        state = _make_state()
        state.tree_filter_active = True
        state.tree_filter_editing = True
        state.tree_filter_query = "abc"
        called = {"query": "", "toggle_help": 0}

        handled = handle_tree_filter_key(
            key="?",
            state=state,
            handle_tree_mouse_wheel=lambda _key: False,
            handle_tree_mouse_click=lambda _key: False,
            toggle_help_panel=lambda: called.__setitem__("toggle_help", called["toggle_help"] + 1),
            close_tree_filter=lambda **_kwargs: None,
            activate_tree_filter_selection=lambda: None,
            move_tree_selection=lambda _direction: False,
            apply_tree_filter_query=lambda query, **_kwargs: called.__setitem__("query", query),
            jump_to_next_content_hit=lambda _direction: False,
        )

        self.assertTrue(handled)
        self.assertEqual(called["query"], "abc?")
        self.assertEqual(called["toggle_help"], 0)

    def test_ctrl_question_toggles_help_while_tree_filter_editing(self) -> None:
        state = _make_state()
        state.tree_filter_active = True
        state.tree_filter_editing = True
        state.tree_filter_query = "abc"
        called = {"apply": 0, "toggle_help": 0}

        handled = handle_tree_filter_key(
            key="CTRL_QUESTION",
            state=state,
            handle_tree_mouse_wheel=lambda _key: False,
            handle_tree_mouse_click=lambda _key: False,
            toggle_help_panel=lambda: called.__setitem__("toggle_help", called["toggle_help"] + 1),
            close_tree_filter=lambda **_kwargs: None,
            activate_tree_filter_selection=lambda: None,
            move_tree_selection=lambda _direction: False,
            apply_tree_filter_query=lambda _query, **_kwargs: called.__setitem__("apply", called["apply"] + 1),
            jump_to_next_content_hit=lambda _direction: False,
        )

        self.assertTrue(handled)
        self.assertEqual(called["apply"], 0)
        self.assertEqual(called["toggle_help"], 1)

    def test_e_launches_selected_directory_when_browser_visible(self) -> None:
        state = _make_state()
        state.browser_visible = True
        state.tree_entries = [TreeEntry(path=Path("/tmp").resolve(), depth=0, is_dir=True)]
        state.selected_idx = 0
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

    def test_e_forces_preview_rebuild_after_successful_edit(self) -> None:
        state = _make_state()
        state.browser_visible = False
        state.current_path = Path("/tmp").resolve()
        refresh_calls: list[dict[str, object]] = []

        should_quit = handle_normal_key(
            key="e",
            term_columns=120,
            state=state,
            current_jump_location=lambda: JumpLocation(path=state.current_path, start=state.start, text_x=state.text_x),
            record_jump_if_changed=lambda _origin: None,
            open_symbol_picker=lambda: None,
            reroot_to_parent=lambda: None,
            reroot_to_selected_target=lambda: None,
            toggle_hidden_files=lambda: None,
            toggle_tree_pane=lambda: None,
            toggle_wrap_mode=lambda: None,
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
            visible_content_rows=lambda: 20,
            rebuild_screen_lines=lambda **_kwargs: None,
            mark_tree_watch_dirty=lambda: None,
            launch_editor_for_path=lambda _path: None,
            jump_to_next_git_modified=lambda _direction: False,
        )

        self.assertFalse(should_quit)
        self.assertEqual(len(refresh_calls), 1)
        self.assertEqual(
            refresh_calls[0],
            {"reset_scroll": True, "reset_dir_budget": True, "force_rebuild": True},
        )


if __name__ == "__main__":
    unittest.main()
