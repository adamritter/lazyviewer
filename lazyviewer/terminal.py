from __future__ import annotations

import contextlib
import os
import termios
import tty


class TerminalController:
    def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
        self.stdin_fd = stdin_fd
        self.stdout_fd = stdout_fd
        self._saved_tty_state = termios.tcgetattr(stdin_fd)

    def enable_tui_mode(self) -> None:
        tty.setraw(self.stdin_fd, termios.TCSAFLUSH)
        # Enter alternate screen, enable mouse reporting, and hide cursor.
        os.write(self.stdout_fd, b"\x1b[?1049h\x1b[?25l\x1b[?1000h\x1b[?1006h")

    def disable_tui_mode(self) -> None:
        # Disable mouse reporting, show cursor, and restore the main screen buffer.
        os.write(self.stdout_fd, b"\x1b[?1000l\x1b[?1006l\x1b[?25h\x1b[?1049l")
        termios.tcsetattr(self.stdin_fd, termios.TCSAFLUSH, self._saved_tty_state)

    @contextlib.contextmanager
    def raw_mode(self):
        try:
            self.enable_tui_mode()
            yield
        finally:
            self.disable_tui_mode()
