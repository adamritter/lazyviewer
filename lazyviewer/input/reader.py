"""Low-level terminal input decoding.

Reads raw bytes from stdin and translates them into normalized key tokens.
Handles ESC-sequence timing, modifier combos, and SGR mouse events.
"""

from __future__ import annotations

import os
import select

ESC_SEQUENCE_TIMEOUT_MS = 25
_PENDING_BYTES: list[bytes] = []
_PENDING_KEYS: list[str] = []

_WHEEL_KINDS = {
    "MOUSE_WHEEL_UP",
    "MOUSE_WHEEL_DOWN",
    "MOUSE_WHEEL_LEFT",
    "MOUSE_WHEEL_RIGHT",
}


def _read_ready_byte(fd: int, timeout_ms: int) -> bytes | None:
    """Read one byte from ``fd`` if data arrives before ``timeout_ms``.

    Returns ``None`` on timeout or EOF so callers can distinguish incomplete
    escape sequences from regular key bytes.
    """
    ready, _, _ = select.select([fd], [], [], max(0.0, timeout_ms / 1000.0))
    if not ready:
        return None
    ch = os.read(fd, 1)
    if not ch:
        return None
    return ch


def read_key(fd: int, timeout_ms: int | None = None) -> str:
    """Decode one terminal input event into a normalized key token.

    This function supports:
    - control keys and printable UTF-8 characters
    - arrow/modified-arrow escape sequences
    - SGR mouse button and wheel events
    - standalone ``ESC`` via short sequence timeout

    Bytes that arrive after a lone ``ESC`` and are not part of ``ESC [`` are
    pushed into ``_PENDING_BYTES`` so the next ``read_key`` call receives them.
    """
    if _PENDING_KEYS:
        return _PENDING_KEYS.pop(0)
    if _PENDING_BYTES:
        ch = _PENDING_BYTES.pop(0)
    else:
        if timeout_ms is not None:
            ready, _, _ = select.select([fd], [], [], max(0.0, timeout_ms / 1000.0))
            if not ready:
                return ""

        ch = os.read(fd, 1)
        if not ch:
            return ""

    if ch == b"\x10":
        return "CTRL_P"
    if ch == b"\x0f":
        return "CTRL_O"
    if ch == b"\x07":
        return "CTRL_G"
    if ch == b"\x1f":
        return "CTRL_QUESTION"
    if ch == b"\t":
        return "TAB"
    if ch in {b"\x08", b"\x7f"}:
        return "BACKSPACE"
    if ch == b"\x15":
        return "CTRL_U"
    if ch == b"\x04":
        return "CTRL_D"
    if ch == b"\x0b":
        return "CTRL_K"
    if ch == b"\r":
        return "ENTER_CR"
    if ch == b"\n":
        return "ENTER_LF"

    if ch != b"\x1b":
        return ch.decode("utf-8", errors="replace")

    # Escape / arrow key sequences.
    seq = _read_ready_byte(fd, ESC_SEQUENCE_TIMEOUT_MS)
    if seq is None:
        return "ESC"
    if seq in {b"b", b"B"}:
        return "ALT_LEFT"
    if seq in {b"f", b"F"}:
        return "ALT_RIGHT"
    if seq != b"[":
        _PENDING_BYTES.append(seq)
        return "ESC"
    seq = _read_ready_byte(fd, ESC_SEQUENCE_TIMEOUT_MS)
    if seq is None:
        return "ESC"
    if seq == b"A":
        return "UP"
    if seq == b"B":
        return "DOWN"
    if seq == b"C":
        return "RIGHT"
    if seq == b"D":
        return "LEFT"
    if seq == b"<":
        # SGR mouse: ESC [ < btn ; col ; row (M/m)
        payload = []
        while True:
            part = _read_ready_byte(fd, ESC_SEQUENCE_TIMEOUT_MS)
            if part is None:
                return "ESC"
            if part in {b"M", b"m"}:
                break
            payload.append(part)
            if len(payload) > 64:
                return "ESC"
        try:
            btn_s, col_s, row_s = b"".join(payload).decode("ascii").split(";")
            btn = int(btn_s)
            col = int(col_s)
            row = int(row_s)
        except Exception:
            return "ESC"
        button = btn & 0b11
        is_wheel = (btn & 0b0100_0000) != 0
        if is_wheel:
            if button == 0:
                return f"MOUSE_WHEEL_UP:{col}:{row}"
            if button == 1:
                return f"MOUSE_WHEEL_DOWN:{col}:{row}"
            if button == 2:
                return f"MOUSE_WHEEL_LEFT:{col}:{row}"
            if button == 3:
                return f"MOUSE_WHEEL_RIGHT:{col}:{row}"
        if button == 0:
            suffix = "DOWN" if part == b"M" else "UP"
            return f"MOUSE_LEFT_{suffix}:{col}:{row}"
        return "MOUSE"
    if seq == b"1":
        seq2 = _read_ready_byte(fd, ESC_SEQUENCE_TIMEOUT_MS)
        if seq2 is None:
            return "ESC"
        if seq2 == b";":
            seq3 = _read_ready_byte(fd, ESC_SEQUENCE_TIMEOUT_MS)
            if seq3 is None:
                return "ESC"
            seq4 = _read_ready_byte(fd, ESC_SEQUENCE_TIMEOUT_MS)
            if seq4 is None:
                return "ESC"
            if seq3 == b"2" and seq4 == b"C":
                return "SHIFT_RIGHT"
            if seq3 == b"2" and seq4 == b"D":
                return "SHIFT_LEFT"
            if seq3 in {b"3", b"9"} and seq4 == b"C":
                return "ALT_RIGHT"
            if seq3 in {b"3", b"9"} and seq4 == b"D":
                return "ALT_LEFT"
        return "ESC"
    return "ESC"


def _wheel_token_parts(key: str) -> tuple[str, int, int, int] | None:
    parts = key.split(":")
    if len(parts) < 3 or parts[0] not in _WHEEL_KINDS:
        return None
    try:
        count = int(parts[3]) if len(parts) >= 4 else 1
        return parts[0], int(parts[1]), int(parts[2]), max(1, count)
    except ValueError:
        return None


def coalesce_mouse_wheel_events(
    fd: int,
    first_key: str,
    *,
    max_events: int = 256,
) -> str:
    """Collapse an already-buffered same-direction wheel burst into one token.

    The next different decoded key is retained for the following ``read_key``
    call, so draining a gesture never swallows keyboard or click input.
    """
    first = _wheel_token_parts(first_key)
    if first is None:
        return first_key
    kind, col, row, count = first
    limit = max(1, max_events)
    while count < limit:
        next_key = read_key(fd, timeout_ms=0)
        if not next_key:
            break
        next_wheel = _wheel_token_parts(next_key)
        if next_wheel is None:
            _PENDING_KEYS.append(next_key)
            break
        next_kind, next_col, next_row, next_count = next_wheel
        if next_kind != kind or next_col != col:
            _PENDING_KEYS.append(next_key)
            break
        row = next_row
        count = min(limit, count + next_count)
    return f"{kind}:{col}:{row}:{count}"


def has_pending_input(fd: int) -> bool:
    """Return whether decoded or operating-system input is ready immediately."""
    if _PENDING_KEYS or _PENDING_BYTES:
        return True
    if not os.isatty(fd):
        return False
    ready, _, _ = select.select([fd], [], [], 0.0)
    return bool(ready)


__all__ = [
    "ESC_SEQUENCE_TIMEOUT_MS",
    "_PENDING_BYTES",
    "_PENDING_KEYS",
    "coalesce_mouse_wheel_events",
    "has_pending_input",
    "read_key",
]
