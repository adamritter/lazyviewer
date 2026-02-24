"""Shared key-handling helper functions."""

from __future__ import annotations

from ..runtime.state import AppState


def effective_max_start(state: AppState, visible_rows: int) -> int:
    """Return max valid vertical scroll offset for current rendered lines."""
    return max(0, len(state.lines) - max(1, visible_rows))


def parse_mouse_col_row(mouse_key: str) -> tuple[int | None, int | None]:
    """Parse ``MOUSE_*:col:row`` key tokens into integer coordinates."""
    parts = mouse_key.split(":")
    if len(parts) < 3:
        return None, None
    try:
        return int(parts[1]), int(parts[2])
    except Exception:
        return None, None


def default_max_horizontal_text_offset() -> int:
    """Fallback clamp for contexts that do not inject source-pane geometry ops."""
    return 10_000_000
