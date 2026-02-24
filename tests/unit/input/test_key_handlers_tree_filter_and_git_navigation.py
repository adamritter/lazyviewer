"""Unit tests for key-handler dispatch behavior.

Verifies mode-aware handling for git/search/help/editor actions.
Keeps keybinding regressions isolated from full runtime integration tests.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lazyviewer.input import (
    handle_normal_key,
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

class KeyHandlersBehaviorTestsPart2(unittest.TestCase):
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
        return handle_normal_key(
            key=key,
            term_columns=120,
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

    def test_p_jumps_to_previous_git_modified_when_enabled(self) -> None:
        state = _make_state()
        state.git_features_enabled = True
        called = {"count": 0, "direction": 0}

        should_quit = self._invoke(
            state=state,
            key="p",
            toggle_git_features=lambda: None,
            jump_to_next_git_modified=lambda direction: called.__setitem__("direction", direction)
            or called.__setitem__("count", called["count"] + 1)
            or True,
        )

        self.assertFalse(should_quit)
        self.assertEqual(called["count"], 1)
        self.assertEqual(called["direction"], -1)

    def test_question_character_is_appended_while_tree_filter_editing(self) -> None:
        state = _make_state()
        state.tree_filter_active = True
        state.tree_filter_mode = "content"
        state.tree_filter_editing = True
        state.tree_filter_query = "abc"
        called = {"query": "", "preview_selection": None, "select_first_file": None, "toggle_help": 0}

        handled = handle_tree_filter_key(
            "?",
            state,
            handle_tree_mouse_wheel=lambda _key: False,
            handle_tree_mouse_click=lambda _key: False,
            toggle_help_panel=lambda: called.__setitem__("toggle_help", called["toggle_help"] + 1),
            close_tree_filter=lambda **_kwargs: None,
            activate_tree_filter_selection=lambda: None,
            move_tree_selection=lambda _direction: False,
            apply_tree_filter_query=lambda query, **kwargs: called.__setitem__("query", query)
            or called.__setitem__("preview_selection", kwargs.get("preview_selection"))
            or called.__setitem__("select_first_file", kwargs.get("select_first_file")),
            jump_to_next_content_hit=lambda _direction: False,
        )

        self.assertTrue(handled)
        self.assertEqual(called["query"], "abc?")
        self.assertFalse(bool(called["preview_selection"]))
        self.assertFalse(bool(called["select_first_file"]))
        self.assertEqual(called["toggle_help"], 0)

    def test_escape_in_content_filter_prompt_requests_restore_to_origin(self) -> None:
        state = _make_state()
        state.tree_filter_active = True
        state.tree_filter_mode = "content"
        state.tree_filter_editing = True
        called: dict[str, object] = {}

        handled = handle_tree_filter_key(
            "ESC",
            state,
            handle_tree_mouse_wheel=lambda _key: False,
            handle_tree_mouse_click=lambda _key: False,
            toggle_help_panel=lambda: None,
            close_tree_filter=lambda **kwargs: called.update(kwargs),
            activate_tree_filter_selection=lambda: None,
            move_tree_selection=lambda _direction: False,
            apply_tree_filter_query=lambda _query, **_kwargs: None,
            jump_to_next_content_hit=lambda _direction: False,
        )

        self.assertTrue(handled)
        self.assertEqual(called["clear_query"], True)
        self.assertEqual(called["restore_origin"], True)

    def test_ctrl_question_toggles_help_while_tree_filter_editing(self) -> None:
        state = _make_state()
        state.tree_filter_active = True
        state.tree_filter_editing = True
        state.tree_filter_query = "abc"
        called = {"apply": 0, "toggle_help": 0}

        handled = handle_tree_filter_key(
            "CTRL_QUESTION",
            state,
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

    def test_p_jumps_to_previous_content_hit_in_content_filter_mode(self) -> None:
        state = _make_state()
        state.tree_filter_active = True
        state.tree_filter_mode = "content"
        state.tree_filter_editing = False
        called = {"direction": 0}

        handled = handle_tree_filter_key(
            "p",
            state,
            handle_tree_mouse_wheel=lambda _key: False,
            handle_tree_mouse_click=lambda _key: False,
            toggle_help_panel=lambda: None,
            close_tree_filter=lambda **_kwargs: None,
            activate_tree_filter_selection=lambda: None,
            move_tree_selection=lambda _direction: False,
            apply_tree_filter_query=lambda _query, **_kwargs: None,
            jump_to_next_content_hit=lambda direction: called.__setitem__("direction", direction) or True,
        )

        self.assertTrue(handled)
        self.assertEqual(called["direction"], -1)
        self.assertTrue(state.dirty)

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
