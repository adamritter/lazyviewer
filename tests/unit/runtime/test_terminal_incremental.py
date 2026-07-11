"""Terminal-controller tests for retained incremental presentation."""

from __future__ import annotations

import unittest
from unittest import mock

from lazyviewer.render.frame import Frame, Rect, Surface
from lazyviewer.runtime.terminal import TerminalController


def _frame(full_text: str, row: str) -> Frame:
    return Frame(
        full_text,
        width=8,
        height=1,
        surfaces=(Surface("pane", Rect(0, 0, 8, 1), (row,)),),
    )


class IncrementalTerminalTests(unittest.TestCase):
    def test_first_frame_is_full_and_second_frame_is_incremental(self) -> None:
        writes: list[bytes] = []
        with mock.patch("lazyviewer.runtime.terminal.termios.tcgetattr", return_value=[0]), mock.patch(
            "lazyviewer.runtime.terminal.os.write",
            side_effect=lambda _fd, data: writes.append(data) or len(data),
        ):
            terminal = TerminalController(stdin_fd=0, stdout_fd=1)
            first_stats = terminal.write_frame(_frame("FULL FIRST", "first"))
            second_stats = terminal.write_frame(_frame("FULL SECOND", "next"))

        self.assertEqual(writes[0], b"FULL FIRST")
        self.assertNotEqual(writes[1], b"FULL SECOND")
        self.assertIn(b"\x1b[1;1H", writes[1])
        self.assertTrue(first_stats.full_repaint)
        self.assertFalse(second_stats.full_repaint)
        self.assertLess(second_stats.bytes_written, len(b"FULL SECOND") + 20)

    def test_screen_invalidation_forces_next_full_repaint(self) -> None:
        writes: list[bytes] = []
        second = _frame("FULL SECOND", "next")
        with mock.patch("lazyviewer.runtime.terminal.termios.tcgetattr", return_value=[0]), mock.patch(
            "lazyviewer.runtime.terminal.os.write",
            side_effect=lambda _fd, data: writes.append(data) or len(data),
        ):
            terminal = TerminalController(stdin_fd=0, stdout_fd=1)
            terminal.write_frame(_frame("FULL FIRST", "first"))
            terminal.invalidate_screen()
            stats = terminal.write_frame(second)

        self.assertEqual(writes[-1], second.data)
        self.assertTrue(stats.full_repaint)


if __name__ == "__main__":
    unittest.main()
