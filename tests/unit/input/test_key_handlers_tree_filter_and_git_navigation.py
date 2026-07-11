"""Unit tests for key-handler dispatch behavior.

Verifies mode-aware handling for git/search/help/editor actions.
Keeps keybinding regressions isolated from full runtime integration tests.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from lazyviewer.input import (
    NormalKeyContext,
    handle_normal_key,
)
from lazyviewer.tree_pane.panels.filter.panel import FilterPanel
from lazyviewer.session.navigation import JumpLocation
from lazyviewer.session import LayoutState, PreviewViewState, SessionState, WorkspaceViewState
from lazyviewer.tree_model import TreeEntry


def handle_tree_filter_key(
    key,
    state,
    *,
    handle_tree_mouse_wheel,
    handle_tree_mouse_click,
    toggle_help_panel,
    **operations,
):
    return FilterPanel(SimpleNamespace(state=state, **operations)).handle_key(
        key,
        handle_tree_mouse_wheel=handle_tree_mouse_wheel,
        handle_tree_mouse_click=handle_tree_mouse_click,
        toggle_help_panel=toggle_help_panel,
    )


def _make_state() -> SessionState:
    root = Path("/tmp").resolve()
    return SessionState(
        workspace=WorkspaceViewState(
            current_path=root, active_root=root, expanded={root}, show_hidden=False,
            entries=[TreeEntry(path=root, depth=0, is_dir=True)], selected=0,
        ),
        preview=PreviewViewState(rendered="", lines=[]),
        layout=LayoutState(left_width=24, right_width=80, usable_rows=24, last_right_width=80),
    )

class KeyHandlersBehaviorTestsPart2(unittest.TestCase):
    def _invoke(
        self,
        *,
        state: SessionState,
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
            def launch_editor_for_path(_path):
                return None
        context = NormalKeyContext(
            state=state,
            current_jump_location=lambda: JumpLocation(path=state.workspace.current_path, start=state.preview.scroll, text_x=state.preview.horizontal_scroll),
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

    def test_n_jumps_to_git_modified_when_enabled(self) -> None:
        state = _make_state()
        state.git.enabled = True
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
        state.git.enabled = True
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
        state.filter.active = True
        state.filter.mode = "content"
        state.filter.editing = True
        state.filter.query = "abc"
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
        state.filter.active = True
        state.filter.mode = "content"
        state.filter.editing = True
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
        state.filter.active = True
        state.filter.editing = True
        state.filter.query = "abc"
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
        state.filter.active = True
        state.filter.mode = "content"
        state.filter.editing = False
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
        self.assertTrue(state.interface.dirty)

    def test_e_launches_selected_directory_when_browser_visible(self) -> None:
        state = _make_state()
        state.layout.browser_visible = True
        state.workspace.entries = [TreeEntry(path=Path("/tmp").resolve(), depth=0, is_dir=True)]
        state.workspace.selected = 0
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
        self.assertEqual(state.workspace.current_path, Path("/tmp").resolve())
