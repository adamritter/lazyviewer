"""Terminal-safe text loading without presentation decisions."""

from __future__ import annotations

import re
from pathlib import Path

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def read_text(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_bytes().decode("utf-8", errors="replace")


def sanitize_text(source: str) -> str:
    if _CONTROL_RE.search(source) is None:
        return source
    out: list[str] = []
    for character in source:
        code = ord(character)
        if character in {"\n", "\r", "\t"}:
            out.append(character)
        elif code < 32 or code == 127 or 0x80 <= code <= 0x9F:
            out.append(f"\\x{code:02x}")
        else:
            out.append(character)
    return "".join(out)
