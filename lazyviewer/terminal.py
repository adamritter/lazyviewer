"""Terminal control helpers for the TUI session.

Owns raw-mode lifecycle, alternate-screen switching, and mouse toggles.
Also wraps Kitty graphics protocol calls used for inline image previews.
"""

from __future__ import annotations

import base64
import contextlib
import os
from pathlib import Path
import termios
import tty


class TerminalController:
    def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
        self.stdin_fd = stdin_fd
        self.stdout_fd = stdout_fd
        self._saved_tty_state = termios.tcgetattr(stdin_fd)
        self._mouse_reporting_enabled = False

    def enable_tui_mode(self) -> None:
        tty.setraw(self.stdin_fd, termios.TCSAFLUSH)
        # Enter alternate screen, enable mouse reporting, and hide cursor.
        os.write(self.stdout_fd, b"\x1b[?1049h\x1b[?25l\x1b[?1000h\x1b[?1002h\x1b[?1006h")
        self._mouse_reporting_enabled = True

    def disable_tui_mode(self) -> None:
        # Disable mouse reporting, show cursor, and restore the main screen buffer.
        os.write(self.stdout_fd, b"\x1b[?1000l\x1b[?1002l\x1b[?1006l\x1b[?25h\x1b[?1049l")
        self._mouse_reporting_enabled = False
        termios.tcsetattr(self.stdin_fd, termios.TCSAFLUSH, self._saved_tty_state)

    def set_mouse_reporting(self, enabled: bool) -> None:
        desired = bool(enabled)
        if desired == self._mouse_reporting_enabled:
            return
        if desired:
            os.write(self.stdout_fd, b"\x1b[?1000h\x1b[?1002h\x1b[?1006h")
        else:
            os.write(self.stdout_fd, b"\x1b[?1000l\x1b[?1002l\x1b[?1006l")
        self._mouse_reporting_enabled = desired

    def supports_kitty_graphics(self) -> bool:
        term = os.environ.get("TERM", "")
        if term == "xterm-kitty":
            return True
        return bool(os.environ.get("KITTY_WINDOW_ID"))

    def kitty_clear_images(self) -> None:
        # Delete all images and placements from the current screen.
        os.write(self.stdout_fd, b"\x1b_Ga=d,d=A,q=2;\x1b\\")

    def kitty_draw_png(
        self,
        image_path: Path,
        col: int,
        row: int,
        width_cells: int,
        height_cells: int,
    ) -> None:
        encoded_path = base64.b64encode(str(image_path).encode("utf-8")).decode("ascii")
        payload = (
            f"\x1b7\x1b[{max(1, row)};{max(1, col)}H"
            f"\x1b_Ga=T,t=f,f=100,q=2,c={max(1, width_cells)},r={max(1, height_cells)};{encoded_path}\x1b\\"
            "\x1b8"
        )
        os.write(self.stdout_fd, payload.encode("ascii"))

    @contextlib.contextmanager
    def raw_mode(self):
        try:
            self.enable_tui_mode()
            yield
        finally:
            self.disable_tui_mode()
