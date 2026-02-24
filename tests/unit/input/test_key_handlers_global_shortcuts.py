"""Unit tests for key-handler dispatch behavior.

Verifies mode-aware handling for git/search/help/editor actions.
Keeps keybinding regressions isolated from full runtime integration tests.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lazyviewer.input import (
    NormalKeyOps,
    PickerKeyCallbacks,
    TreeFilterKeyCallbacks,
    handle_normal_key,
    handle_picker_key,
    handle_tree_filter_key,
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

class KeyHandlersBehaviorTestsPart1(unittest.TestCase):
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
        ops = NormalKeyOps(
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
        return handle_normal_key(
            key=key,
            term_columns=120,
            state=state,
            ops=ops,
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

    def test_shift_s_toggles_tree_size_labels(self) -> None:
        state = _make_state()
        called = {"count": 0}

        should_quit = self._invoke(
            state=state,
            key="S",
            toggle_git_features=lambda: None,
            toggle_tree_size_labels=lambda: called.__setitem__("count", called["count"] + 1),
            jump_to_next_git_modified=lambda _direction: False,
        )

        self.assertFalse(should_quit)
        self.assertEqual(called["count"], 1)

    def test_lower_s_keeps_symbol_picker_binding(self) -> None:
        state = _make_state()
        called = {"symbol": 0, "sizes": 0}

        should_quit = self._invoke(
            state=state,
            key="s",
            toggle_git_features=lambda: None,
            toggle_tree_size_labels=lambda: called.__setitem__("sizes", called["sizes"] + 1),
            jump_to_next_git_modified=lambda _direction: False,
            open_symbol_picker=lambda: called.__setitem__("symbol", called["symbol"] + 1),
        )

        self.assertFalse(should_quit)
        self.assertEqual(called["symbol"], 1)
        self.assertEqual(called["sizes"], 0)

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
            "\x03",
            state,
            0.25,
            PickerKeyCallbacks(
                close_picker=lambda: close_calls.__setitem__("count", close_calls["count"] + 1),
                refresh_command_picker_matches=lambda **_kwargs: None,
                activate_picker_selection=lambda: False,
                visible_content_rows=lambda: 20,
                refresh_active_picker_matches=lambda **_kwargs: None,
            ),
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
