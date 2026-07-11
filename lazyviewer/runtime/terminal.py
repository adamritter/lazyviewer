"""Terminal control helpers for the TUI session.

Owns raw-mode lifecycle, alternate-screen switching, and mouse toggles.
Also wraps Kitty graphics protocol calls used for inline image previews.
"""

from __future__ import annotations

import base64
import contextlib
import os
from dataclasses import dataclass
from pathlib import Path
import termios
import tty

from ..render.diff import diff_frames
from ..render.frame import Frame


@dataclass(frozen=True, slots=True)
class PresentStats:
    """Measurements for the most recent terminal presentation."""

    bytes_written: int
    rows_touched: int
    surfaces_touched: int
    full_repaint: bool


class TerminalController:
    """Manage terminal mode transitions and optional kitty image rendering."""

    def __init__(self, stdin_fd: int, stdout_fd: int) -> None:
        """Capture tty state and bind stdin/stdout file descriptors."""
        self.stdin_fd = stdin_fd
        self.stdout_fd = stdout_fd
        self._saved_tty_state = termios.tcgetattr(stdin_fd)
        self._mouse_reporting_enabled = False
        self._presented_frame: Frame | None = None
        self.last_present_stats = PresentStats(0, 0, 0, False)

    def enable_tui_mode(self) -> None:
        """Enter raw alternate-screen mode with mouse reporting enabled."""
        tty.setraw(self.stdin_fd, termios.TCSAFLUSH)
        # Enter alternate screen, enable mouse reporting, and hide cursor.
        os.write(self.stdout_fd, b"\x1b[?1049h\x1b[?25l\x1b[?1000h\x1b[?1002h\x1b[?1006h")
        self._mouse_reporting_enabled = True
        self.invalidate_screen()

    def disable_tui_mode(self) -> None:
        """Restore normal terminal state and disable TUI mouse mode."""
        # Disable mouse reporting, show cursor, and restore the main screen buffer.
        os.write(self.stdout_fd, b"\x1b[?1000l\x1b[?1002l\x1b[?1006l\x1b[?25h\x1b[?1049l")
        self._mouse_reporting_enabled = False
        self.invalidate_screen()
        termios.tcsetattr(self.stdin_fd, termios.TCSAFLUSH, self._saved_tty_state)

    def set_mouse_reporting(self, enabled: bool) -> None:
        """Toggle terminal mouse tracking without changing other TUI state."""
        desired = bool(enabled)
        if desired == self._mouse_reporting_enabled:
            return
        if desired:
            os.write(self.stdout_fd, b"\x1b[?1000h\x1b[?1002h\x1b[?1006h")
        else:
            os.write(self.stdout_fd, b"\x1b[?1000l\x1b[?1002l\x1b[?1006l")
        self._mouse_reporting_enabled = desired

    def invalidate_screen(self) -> None:
        """Forget retained terminal contents so the next frame repaints fully."""
        self._presented_frame = None

    def write_frame(self, frame: Frame) -> PresentStats:
        """Present one frame atomically, emitting only changed surface rows.

        The first structured frame, a geometry change, or an explicit screen
        invalidation uses the frame's canonical full repaint.  Stable layouts
        use absolute cursor addressing and exact-width pane-row updates.
        """
        patch = diff_frames(self._presented_frame, frame)
        if patch.data:
            os.write(self.stdout_fd, patch.data)
        stats = PresentStats(
            bytes_written=len(patch.data),
            rows_touched=patch.rows_touched,
            surfaces_touched=patch.surfaces_touched,
            full_repaint=patch.full_repaint,
        )
        self._presented_frame = frame if frame.is_structured else None
        self.last_present_stats = stats
        return stats

    def supports_kitty_graphics(self) -> bool:
        """Return whether environment appears to support kitty graphics protocol."""
        term = os.environ.get("TERM", "")
        if term == "xterm-kitty":
            return True
        return bool(os.environ.get("KITTY_WINDOW_ID"))

    def kitty_clear_images(self) -> None:
        """Clear all kitty inline images from current screen."""
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
        """Draw a PNG via kitty graphics protocol at cell-based coordinates."""
        encoded_path = base64.b64encode(str(image_path).encode("utf-8")).decode("ascii")
        payload = (
            f"\x1b7\x1b[{max(1, row)};{max(1, col)}H"
            f"\x1b_Ga=T,t=f,f=100,q=2,c={max(1, width_cells)},r={max(1, height_cells)};{encoded_path}\x1b\\"
            "\x1b8"
        )
        os.write(self.stdout_fd, payload.encode("ascii"))

    @contextlib.contextmanager
    def raw_mode(self):
        """Context manager that brackets code with TUI enter/exit calls."""
        try:
            self.enable_tui_mode()
            yield
        finally:
            self.disable_tui_mode()


__all__ = ["PresentStats", "TerminalController"]
