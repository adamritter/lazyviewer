from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import unittest
from unittest import mock

from lazyviewer.runtime_loop import run_main_loop
from lazyviewer.state import AppState
from lazyviewer.terminal import TerminalController
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


class _FakeTerminal:
    def __init__(self) -> None:
        self.mouse_reporting_calls: list[bool] = []

    @contextmanager
    def raw_mode(self):
        yield

    def set_mouse_reporting(self, enabled: bool) -> None:
        self.mouse_reporting_calls.append(bool(enabled))

    def kitty_clear_images(self) -> None:
        pass

    def kitty_draw_png(self, *_args, **_kwargs) -> None:
        pass


class RuntimeLoopBehaviorTests(unittest.TestCase):
    def test_runtime_loop_requests_mouse_reporting_enabled(self) -> None:
        state = _make_state()
        terminal = _FakeTerminal()
        keys = iter(["q"])

        with mock.patch(
            "lazyviewer.runtime_loop.shutil.get_terminal_size",
            return_value=mock.Mock(columns=120, lines=40),
        ), mock.patch(
            "lazyviewer.runtime_loop.read_key",
            side_effect=lambda *_args, **_kwargs: next(keys),
        ), mock.patch(
            "lazyviewer.runtime_loop.render_dual_page_context",
            return_value=None,
        ):
            run_main_loop(
                state=state,
                terminal=terminal,  # type: ignore[arg-type]
                stdin_fd=0,
                double_click_seconds=0.35,
                filter_cursor_blink_seconds=0.5,
                tree_filter_spinner_frame_seconds=0.12,
                get_tree_filter_loading_until=lambda: 0.0,
                tree_view_rows=lambda: 20,
                tree_filter_prompt_prefix=lambda: "p>",
                tree_filter_placeholder=lambda: "type",
                visible_content_rows=lambda: 20,
                rebuild_screen_lines=lambda **_kwargs: None,
                maybe_refresh_tree_watch=lambda: None,
                maybe_refresh_git_watch=lambda: None,
                refresh_git_status_overlay=lambda **_kwargs: None,
                current_preview_image_path=lambda: None,
                current_preview_image_geometry=lambda _columns: (1, 1, 1, 1),
                open_tree_filter=lambda _mode: None,
                open_command_picker=lambda: None,
                close_picker=lambda **_kwargs: None,
                refresh_command_picker_matches=lambda **_kwargs: None,
                activate_picker_selection=lambda: False,
                refresh_active_picker_matches=lambda **_kwargs: None,
                handle_tree_mouse_wheel=lambda _key: False,
                handle_tree_mouse_click=lambda _key: False,
                toggle_help_panel=lambda: None,
                close_tree_filter=lambda **_kwargs: None,
                activate_tree_filter_selection=lambda: None,
                move_tree_selection=lambda _direction: False,
                apply_tree_filter_query=lambda *_args, **_kwargs: None,
                jump_to_next_content_hit=lambda _direction: False,
                set_named_mark=lambda _key: False,
                jump_to_named_mark=lambda _key: False,
                jump_back_in_history=lambda: False,
                jump_forward_in_history=lambda: False,
                handle_normal_key=lambda key, _columns: key == "q",
                save_left_pane_width=lambda _total, _left: None,
            )

        self.assertTrue(terminal.mouse_reporting_calls)
        self.assertTrue(terminal.mouse_reporting_calls[0])

    def test_terminal_raw_mode_still_brackets_with_mouse_on_off_sequences(self) -> None:
        state = _make_state()
        writes: list[bytes] = []
        keys = iter(["q"])

        with mock.patch("lazyviewer.terminal.termios.tcgetattr", return_value=[0]), mock.patch(
            "lazyviewer.terminal.tty.setraw"
        ), mock.patch("lazyviewer.terminal.termios.tcsetattr"), mock.patch(
            "lazyviewer.terminal.os.write",
            side_effect=lambda _fd, data: writes.append(data) or len(data),
        ), mock.patch(
            "lazyviewer.runtime_loop.shutil.get_terminal_size",
            return_value=mock.Mock(columns=120, lines=40),
        ), mock.patch(
            "lazyviewer.runtime_loop.read_key",
            side_effect=lambda *_args, **_kwargs: next(keys),
        ), mock.patch(
            "lazyviewer.runtime_loop.render_dual_page_context",
            return_value=None,
        ):
            terminal = TerminalController(stdin_fd=0, stdout_fd=1)
            run_main_loop(
                state=state,
                terminal=terminal,
                stdin_fd=0,
                double_click_seconds=0.35,
                filter_cursor_blink_seconds=0.5,
                tree_filter_spinner_frame_seconds=0.12,
                get_tree_filter_loading_until=lambda: 0.0,
                tree_view_rows=lambda: 20,
                tree_filter_prompt_prefix=lambda: "p>",
                tree_filter_placeholder=lambda: "type",
                visible_content_rows=lambda: 20,
                rebuild_screen_lines=lambda **_kwargs: None,
                maybe_refresh_tree_watch=lambda: None,
                maybe_refresh_git_watch=lambda: None,
                refresh_git_status_overlay=lambda **_kwargs: None,
                current_preview_image_path=lambda: None,
                current_preview_image_geometry=lambda _columns: (1, 1, 1, 1),
                open_tree_filter=lambda _mode: None,
                open_command_picker=lambda: None,
                close_picker=lambda **_kwargs: None,
                refresh_command_picker_matches=lambda **_kwargs: None,
                activate_picker_selection=lambda: False,
                refresh_active_picker_matches=lambda **_kwargs: None,
                handle_tree_mouse_wheel=lambda _key: False,
                handle_tree_mouse_click=lambda _key: False,
                toggle_help_panel=lambda: None,
                close_tree_filter=lambda **_kwargs: None,
                activate_tree_filter_selection=lambda: None,
                move_tree_selection=lambda _direction: False,
                apply_tree_filter_query=lambda *_args, **_kwargs: None,
                jump_to_next_content_hit=lambda _direction: False,
                set_named_mark=lambda _key: False,
                jump_to_named_mark=lambda _key: False,
                jump_back_in_history=lambda: False,
                jump_forward_in_history=lambda: False,
                handle_normal_key=lambda key, _columns: key == "q",
                save_left_pane_width=lambda _total, _left: None,
            )

        self.assertTrue(writes)
        self.assertEqual(writes[0], b"\x1b[?1049h\x1b[?25l\x1b[?1000h\x1b[?1002h\x1b[?1006h")
        self.assertTrue(any(b"\x1b[?1000l\x1b[?1002l\x1b[?1006l" in chunk for chunk in writes))

    def test_ctrl_y_byte_no_longer_toggles_mouse_reporting(self) -> None:
        state = _make_state()
        terminal = _FakeTerminal()
        keys = iter(["\x19", "q"])

        with mock.patch(
            "lazyviewer.runtime_loop.shutil.get_terminal_size",
            return_value=mock.Mock(columns=120, lines=40),
        ), mock.patch(
            "lazyviewer.runtime_loop.read_key",
            side_effect=lambda *_args, **_kwargs: next(keys),
        ), mock.patch(
            "lazyviewer.runtime_loop.render_dual_page_context",
            return_value=None,
        ):
            run_main_loop(
                state=state,
                terminal=terminal,  # type: ignore[arg-type]
                stdin_fd=0,
                double_click_seconds=0.35,
                filter_cursor_blink_seconds=0.5,
                tree_filter_spinner_frame_seconds=0.12,
                get_tree_filter_loading_until=lambda: 0.0,
                tree_view_rows=lambda: 20,
                tree_filter_prompt_prefix=lambda: "p>",
                tree_filter_placeholder=lambda: "type",
                visible_content_rows=lambda: 20,
                rebuild_screen_lines=lambda **_kwargs: None,
                maybe_refresh_tree_watch=lambda: None,
                maybe_refresh_git_watch=lambda: None,
                refresh_git_status_overlay=lambda **_kwargs: None,
                current_preview_image_path=lambda: None,
                current_preview_image_geometry=lambda _columns: (1, 1, 1, 1),
                open_tree_filter=lambda _mode: None,
                open_command_picker=lambda: None,
                close_picker=lambda **_kwargs: None,
                refresh_command_picker_matches=lambda **_kwargs: None,
                activate_picker_selection=lambda: False,
                refresh_active_picker_matches=lambda **_kwargs: None,
                handle_tree_mouse_wheel=lambda _key: False,
                handle_tree_mouse_click=lambda _key: False,
                toggle_help_panel=lambda: None,
                close_tree_filter=lambda **_kwargs: None,
                activate_tree_filter_selection=lambda: None,
                move_tree_selection=lambda _direction: False,
                apply_tree_filter_query=lambda *_args, **_kwargs: None,
                jump_to_next_content_hit=lambda _direction: False,
                set_named_mark=lambda _key: False,
                jump_to_named_mark=lambda _key: False,
                jump_back_in_history=lambda: False,
                jump_forward_in_history=lambda: False,
                handle_normal_key=lambda key, _columns: key == "q",
                save_left_pane_width=lambda _total, _left: None,
            )

        self.assertTrue(terminal.mouse_reporting_calls)
        self.assertNotIn(False, terminal.mouse_reporting_calls)

    def test_keyboard_interrupt_during_key_read_is_ignored(self) -> None:
        state = _make_state()
        terminal = _FakeTerminal()
        events = iter([KeyboardInterrupt(), "q"])
        read_calls = {"count": 0}

        def _read_key(*_args, **_kwargs):
            read_calls["count"] += 1
            event = next(events)
            if isinstance(event, BaseException):
                raise event
            return event

        with mock.patch(
            "lazyviewer.runtime_loop.shutil.get_terminal_size",
            return_value=mock.Mock(columns=120, lines=40),
        ), mock.patch(
            "lazyviewer.runtime_loop.read_key",
            side_effect=_read_key,
        ), mock.patch(
            "lazyviewer.runtime_loop.render_dual_page_context",
            return_value=None,
        ):
            run_main_loop(
                state=state,
                terminal=terminal,  # type: ignore[arg-type]
                stdin_fd=0,
                double_click_seconds=0.35,
                filter_cursor_blink_seconds=0.5,
                tree_filter_spinner_frame_seconds=0.12,
                get_tree_filter_loading_until=lambda: 0.0,
                tree_view_rows=lambda: 20,
                tree_filter_prompt_prefix=lambda: "p>",
                tree_filter_placeholder=lambda: "type",
                visible_content_rows=lambda: 20,
                rebuild_screen_lines=lambda **_kwargs: None,
                maybe_refresh_tree_watch=lambda: None,
                maybe_refresh_git_watch=lambda: None,
                refresh_git_status_overlay=lambda **_kwargs: None,
                current_preview_image_path=lambda: None,
                current_preview_image_geometry=lambda _columns: (1, 1, 1, 1),
                open_tree_filter=lambda _mode: None,
                open_command_picker=lambda: None,
                close_picker=lambda **_kwargs: None,
                refresh_command_picker_matches=lambda **_kwargs: None,
                activate_picker_selection=lambda: False,
                refresh_active_picker_matches=lambda **_kwargs: None,
                handle_tree_mouse_wheel=lambda _key: False,
                handle_tree_mouse_click=lambda _key: False,
                toggle_help_panel=lambda: None,
                close_tree_filter=lambda **_kwargs: None,
                activate_tree_filter_selection=lambda: None,
                move_tree_selection=lambda _direction: False,
                apply_tree_filter_query=lambda *_args, **_kwargs: None,
                jump_to_next_content_hit=lambda _direction: False,
                set_named_mark=lambda _key: False,
                jump_to_named_mark=lambda _key: False,
                jump_back_in_history=lambda: False,
                jump_forward_in_history=lambda: False,
                handle_normal_key=lambda key, _columns: key == "q",
                save_left_pane_width=lambda _total, _left: None,
            )

        self.assertGreaterEqual(read_calls["count"], 2)


if __name__ == "__main__":
    unittest.main()
