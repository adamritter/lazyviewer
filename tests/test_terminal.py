from __future__ import annotations

import termios
import unittest
from unittest import mock

from lazyviewer.terminal import TerminalController


class TerminalBehaviorTests(unittest.TestCase):
    def test_enable_and_disable_tui_mode_toggle_mouse_and_cursor_sequences(self) -> None:
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
        self.assertEqual(write_mock.call_args_list[0].args, (1, b"\x1b[?25l\x1b[?1000h\x1b[?1006h"))
        self.assertEqual(write_mock.call_args_list[1].args, (1, b"\x1b[?1000l\x1b[?1006l\x1b[?25h"))
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


if __name__ == "__main__":
    unittest.main()
