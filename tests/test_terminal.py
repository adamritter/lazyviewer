"""Tests for terminal mode and Kitty graphics control sequences.

Verifies raw-mode lifecycle safety and expected escape-command payloads.
These guard the low-level terminal contract used by the runtime loop.
"""

from __future__ import annotations

import base64
from pathlib import Path
import termios
import unittest
from unittest import mock

from lazyviewer.terminal import TerminalController


class TerminalBehaviorTests(unittest.TestCase):
    def test_enable_and_disable_tui_mode_use_alternate_screen_sequences(self) -> None:
        saved_state = [1, 2, 3]

        with mock.patch("lazyviewer.terminal.termios.tcgetattr", return_value=saved_state), mock.patch(
            "lazyviewer.terminal.tty.setraw"
        ) as setraw_mock, mock.patch("lazyviewer.terminal.os.write") as write_mock, mock.patch(
            "lazyviewer.terminal.termios.tcsetattr"
        ) as setattr_mock:
            controller = TerminalController(stdin_fd=0, stdout_fd=1)
            controller.enable_tui_mode()
            controller.disable_tui_mode()

        setraw_mock.assert_called_once_with(0, termios.TCSAFLUSH)
        self.assertEqual(write_mock.call_args_list[0].args, (1, b"\x1b[?1049h\x1b[?25l\x1b[?1000h\x1b[?1006h"))
        self.assertEqual(write_mock.call_args_list[1].args, (1, b"\x1b[?1000l\x1b[?1006l\x1b[?25h\x1b[?1049l"))
        setattr_mock.assert_called_once_with(0, termios.TCSAFLUSH, saved_state)

    def test_raw_mode_restores_terminal_after_exception(self) -> None:
        with mock.patch("lazyviewer.terminal.termios.tcgetattr", return_value=[0]):
            controller = TerminalController(stdin_fd=0, stdout_fd=1)

        with mock.patch.object(controller, "enable_tui_mode") as enable_mock, mock.patch.object(
            controller, "disable_tui_mode"
        ) as disable_mock:
            with self.assertRaises(RuntimeError):
                with controller.raw_mode():
                    raise RuntimeError("boom")

        enable_mock.assert_called_once()
        disable_mock.assert_called_once()

    def test_supports_kitty_graphics_checks_term_and_env(self) -> None:
        with mock.patch("lazyviewer.terminal.termios.tcgetattr", return_value=[0]):
            controller = TerminalController(stdin_fd=0, stdout_fd=1)

        with mock.patch.dict("lazyviewer.terminal.os.environ", {"TERM": "xterm-kitty"}, clear=True):
            self.assertTrue(controller.supports_kitty_graphics())

        with mock.patch.dict("lazyviewer.terminal.os.environ", {"KITTY_WINDOW_ID": "12"}, clear=True):
            self.assertTrue(controller.supports_kitty_graphics())

        with mock.patch.dict("lazyviewer.terminal.os.environ", {"TERM": "xterm-256color"}, clear=True):
            self.assertFalse(controller.supports_kitty_graphics())

    def test_set_mouse_reporting_toggles_and_is_idempotent(self) -> None:
        with mock.patch("lazyviewer.terminal.termios.tcgetattr", return_value=[0]), mock.patch(
            "lazyviewer.terminal.os.write"
        ) as write_mock:
            controller = TerminalController(stdin_fd=0, stdout_fd=1)
            controller.set_mouse_reporting(False)
            controller.set_mouse_reporting(True)
            controller.set_mouse_reporting(True)
            controller.set_mouse_reporting(False)

        self.assertEqual(
            write_mock.call_args_list,
            [
                mock.call(1, b"\x1b[?1000h\x1b[?1006h"),
                mock.call(1, b"\x1b[?1000l\x1b[?1006l"),
            ],
        )

    def test_kitty_graphics_commands_emit_expected_sequences(self) -> None:
        with mock.patch("lazyviewer.terminal.termios.tcgetattr", return_value=[0]), mock.patch(
            "lazyviewer.terminal.os.write"
        ) as write_mock:
            controller = TerminalController(stdin_fd=0, stdout_fd=1)
            controller.kitty_clear_images()
            controller.kitty_draw_png(Path("/tmp/demo.png"), col=5, row=3, width_cells=20, height_cells=7)

        self.assertEqual(write_mock.call_args_list[0].args, (1, b"\x1b_Ga=d,d=A,q=2;\x1b\\"))
        encoded = base64.b64encode(b"/tmp/demo.png").decode("ascii")
        self.assertEqual(
            write_mock.call_args_list[1].args,
            (
                1,
                (
                    f"\x1b7\x1b[3;5H"
                    f"\x1b_Ga=T,t=f,f=100,q=2,c=20,r=7;{encoded}\x1b\\"
                    "\x1b8"
                ).encode("ascii"),
            ),
        )


if __name__ == "__main__":
    unittest.main()
