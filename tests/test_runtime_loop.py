from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import time
import unittest
from unittest import mock

from lazyviewer.runtime import RuntimeLoopCallbacks, RuntimeLoopTiming, run_main_loop
from lazyviewer.state import AppState
from lazyviewer.runtime.terminal import TerminalController
from lazyviewer.tree_pane.model import TreeEntry


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


def _loop_timing() -> RuntimeLoopTiming:
    return RuntimeLoopTiming(
        double_click_seconds=0.35,
        filter_cursor_blink_seconds=0.5,
        tree_filter_spinner_frame_seconds=0.12,
    )


def _loop_callbacks(
    *,
    handle_normal_key,
    **overrides,
) -> RuntimeLoopCallbacks:
    base = RuntimeLoopCallbacks(
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
        handle_normal_key=handle_normal_key,
        save_left_pane_width=lambda _total, _left: None,
        tick_source_selection_drag=None,
    )
    if not overrides:
        return base
    return replace(base, **overrides)


class RuntimeLoopBehaviorTests(unittest.TestCase):
    def test_runtime_loop_requests_mouse_reporting_enabled(self) -> None:
        state = _make_state()
        terminal = _FakeTerminal()
        keys = iter(["q"])

        with mock.patch(
            "lazyviewer.runtime.loop.shutil.get_terminal_size",
            return_value=mock.Mock(columns=120, lines=40),
        ), mock.patch(
            "lazyviewer.runtime.loop.read_key",
            side_effect=lambda *_args, **_kwargs: next(keys),
        ), mock.patch(
            "lazyviewer.runtime.loop.render_dual_page_context",
            return_value=None,
        ):
            run_main_loop(
                state=state,
                terminal=terminal,  # type: ignore[arg-type]
                stdin_fd=0,
                timing=_loop_timing(),
                callbacks=_loop_callbacks(handle_normal_key=lambda key, _columns: key == "q"),
            )

        self.assertTrue(terminal.mouse_reporting_calls)
        self.assertTrue(terminal.mouse_reporting_calls[0])

    def test_terminal_raw_mode_still_brackets_with_mouse_on_off_sequences(self) -> None:
        state = _make_state()
        writes: list[bytes] = []
        keys = iter(["q"])

        with mock.patch("lazyviewer.runtime.terminal.termios.tcgetattr", return_value=[0]), mock.patch(
            "lazyviewer.runtime.terminal.tty.setraw"
        ), mock.patch("lazyviewer.runtime.terminal.termios.tcsetattr"), mock.patch(
            "lazyviewer.runtime.terminal.os.write",
            side_effect=lambda _fd, data: writes.append(data) or len(data),
        ), mock.patch(
            "lazyviewer.runtime.loop.shutil.get_terminal_size",
            return_value=mock.Mock(columns=120, lines=40),
        ), mock.patch(
            "lazyviewer.runtime.loop.read_key",
            side_effect=lambda *_args, **_kwargs: next(keys),
        ), mock.patch(
            "lazyviewer.runtime.loop.render_dual_page_context",
            return_value=None,
        ):
            terminal = TerminalController(stdin_fd=0, stdout_fd=1)
            run_main_loop(
                state=state,
                terminal=terminal,
                stdin_fd=0,
                timing=_loop_timing(),
                callbacks=_loop_callbacks(handle_normal_key=lambda key, _columns: key == "q"),
            )

        self.assertTrue(writes)
        self.assertEqual(writes[0], b"\x1b[?1049h\x1b[?25l\x1b[?1000h\x1b[?1002h\x1b[?1006h")
        self.assertTrue(any(b"\x1b[?1000l\x1b[?1002l\x1b[?1006l" in chunk for chunk in writes))

    def test_ctrl_y_byte_no_longer_toggles_mouse_reporting(self) -> None:
        state = _make_state()
        terminal = _FakeTerminal()
        keys = iter(["\x19", "q"])

        with mock.patch(
            "lazyviewer.runtime.loop.shutil.get_terminal_size",
            return_value=mock.Mock(columns=120, lines=40),
        ), mock.patch(
            "lazyviewer.runtime.loop.read_key",
            side_effect=lambda *_args, **_kwargs: next(keys),
        ), mock.patch(
            "lazyviewer.runtime.loop.render_dual_page_context",
            return_value=None,
        ):
            run_main_loop(
                state=state,
                terminal=terminal,  # type: ignore[arg-type]
                stdin_fd=0,
                timing=_loop_timing(),
                callbacks=_loop_callbacks(handle_normal_key=lambda key, _columns: key == "q"),
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
            "lazyviewer.runtime.loop.shutil.get_terminal_size",
            return_value=mock.Mock(columns=120, lines=40),
        ), mock.patch(
            "lazyviewer.runtime.loop.read_key",
            side_effect=_read_key,
        ), mock.patch(
            "lazyviewer.runtime.loop.render_dual_page_context",
            return_value=None,
        ):
            run_main_loop(
                state=state,
                terminal=terminal,  # type: ignore[arg-type]
                stdin_fd=0,
                timing=_loop_timing(),
                callbacks=_loop_callbacks(handle_normal_key=lambda key, _columns: key == "q"),
            )

        self.assertGreaterEqual(read_calls["count"], 2)

    def test_dirty_frame_renders_before_idle_refresh_callbacks(self) -> None:
        state = _make_state()
        terminal = _FakeTerminal()
        keys = iter(["", "q"])
        marks: dict[str, float] = {}

        def slow_tree_refresh() -> None:
            marks.setdefault("tree_refresh_start", time.perf_counter())
            time.sleep(0.06)

        def slow_git_refresh() -> None:
            marks.setdefault("git_refresh_start", time.perf_counter())
            time.sleep(0.06)

        def slow_overlay_refresh(**_kwargs) -> None:
            marks.setdefault("overlay_refresh_start", time.perf_counter())
            time.sleep(0.06)

        def fake_render(_context) -> None:
            marks.setdefault("render_start", time.perf_counter())

        start = time.perf_counter()
        with mock.patch(
            "lazyviewer.runtime.loop.shutil.get_terminal_size",
            return_value=mock.Mock(columns=120, lines=40),
        ), mock.patch(
            "lazyviewer.runtime.loop.read_key",
            side_effect=lambda *_args, **_kwargs: next(keys),
        ), mock.patch(
            "lazyviewer.runtime.loop.render_dual_page_context",
            side_effect=fake_render,
        ):
            run_main_loop(
                state=state,
                terminal=terminal,  # type: ignore[arg-type]
                stdin_fd=0,
                timing=_loop_timing(),
                callbacks=_loop_callbacks(
                    handle_normal_key=lambda key, _columns: key == "q",
                    maybe_refresh_tree_watch=slow_tree_refresh,
                    maybe_refresh_git_watch=slow_git_refresh,
                    refresh_git_status_overlay=slow_overlay_refresh,
                ),
            )

        self.assertIn("render_start", marks)
        self.assertIn("tree_refresh_start", marks)
        self.assertLess(marks["render_start"] - start, 0.05)
        self.assertGreater(marks["tree_refresh_start"], marks["render_start"])

    def test_immediate_keypress_skips_idle_refresh_callbacks(self) -> None:
        state = _make_state()
        terminal = _FakeTerminal()
        keys = iter(["q"])
        calls = {"tree": 0, "git": 0, "overlay": 0}

        with mock.patch(
            "lazyviewer.runtime.loop.shutil.get_terminal_size",
            return_value=mock.Mock(columns=120, lines=40),
        ), mock.patch(
            "lazyviewer.runtime.loop.read_key",
            side_effect=lambda *_args, **_kwargs: next(keys),
        ), mock.patch(
            "lazyviewer.runtime.loop.render_dual_page_context",
            return_value=None,
        ):
            run_main_loop(
                state=state,
                terminal=terminal,  # type: ignore[arg-type]
                stdin_fd=0,
                timing=_loop_timing(),
                callbacks=_loop_callbacks(
                    handle_normal_key=lambda key, _columns: key == "q",
                    maybe_refresh_tree_watch=lambda: calls.__setitem__("tree", calls["tree"] + 1),
                    maybe_refresh_git_watch=lambda: calls.__setitem__("git", calls["git"] + 1),
                    refresh_git_status_overlay=lambda **_kwargs: calls.__setitem__(
                        "overlay", calls["overlay"] + 1
                    ),
                ),
            )

        self.assertEqual(calls, {"tree": 0, "git": 0, "overlay": 0})


if __name__ == "__main__":
    unittest.main()
