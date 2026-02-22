from __future__ import annotations

import unittest
from pathlib import Path

from lazyviewer.key_handlers import handle_normal_key
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
    ) -> bool:
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
            handle_tree_mouse_wheel=lambda _key: False,
            handle_tree_mouse_click=lambda _key: False,
            move_tree_selection=lambda _delta: False,
            rebuild_tree_entries=lambda **_kwargs: None,
            preview_selected_entry=lambda **_kwargs: None,
            refresh_rendered_for_current_path=lambda **_kwargs: None,
            refresh_git_status_overlay=lambda **_kwargs: None,
            maybe_grow_directory_preview=lambda: False,
            visible_content_rows=lambda: 20,
            rebuild_screen_lines=lambda **_kwargs: None,
            mark_tree_watch_dirty=lambda: None,
            launch_editor_for_path=lambda _path: None,
            jump_to_next_git_modified=jump_to_next_git_modified,
        )

    def test_ctrl_g_toggles_git_features(self) -> None:
        state = _make_state()
        called = {"count": 0}

        should_quit = self._invoke(
            state=state,
            key="CTRL_G",
            toggle_git_features=lambda: called.__setitem__("count", called["count"] + 1),
            jump_to_next_git_modified=lambda _direction: False,
        )

        self.assertFalse(should_quit)
        self.assertEqual(called["count"], 1)

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


if __name__ == "__main__":
    unittest.main()
