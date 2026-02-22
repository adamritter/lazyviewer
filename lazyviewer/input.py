from __future__ import annotations

import os
import select


def read_key(fd: int, timeout_ms: int | None = None) -> str:
    if timeout_ms is not None:
        ready, _, _ = select.select([fd], [], [], max(0.0, timeout_ms / 1000.0))
        if not ready:
            return ""

    ch = os.read(fd, 1)
    if not ch:
        return ""

    if ch == b"\x15":
        return "CTRL_U"
    if ch == b"\r":
        return "ENTER_CR"
    if ch == b"\n":
        return "ENTER_LF"

    if ch != b"\x1b":
        return ch.decode("utf-8", errors="replace")

    # Escape / arrow key sequences.
    seq = os.read(fd, 1)
    if not seq:
        return "ESC"
    if seq != b"[":
        return "ESC"
    seq = os.read(fd, 1)
    if not seq:
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
            part = os.read(fd, 1)
            if not part:
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
        if btn == 64:
            return f"MOUSE_WHEEL_UP:{col}:{row}"
        if btn == 65:
            return f"MOUSE_WHEEL_DOWN:{col}:{row}"
        button = btn & 0b11
        if button == 0:
            suffix = "DOWN" if part == b"M" else "UP"
            return f"MOUSE_LEFT_{suffix}:{col}:{row}"
        return "MOUSE"
    if seq == b"1":
        seq2 = os.read(fd, 1)
        if not seq2:
            return "ESC"
        if seq2 == b";":
            seq3 = os.read(fd, 1)
            if not seq3:
                return "ESC"
            seq4 = os.read(fd, 1)
            if not seq4:
                return "ESC"
            if seq3 == b"2" and seq4 == b"C":
                return "SHIFT_RIGHT"
            if seq3 == b"2" and seq4 == b"D":
                return "SHIFT_LEFT"
        return "ESC"
    return "ESC"
