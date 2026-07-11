"""ANSI presentation helpers for semantic diff documents."""

from __future__ import annotations

import re

_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")
_ADDED_BG_SGR = "48;2;36;74;52"
_REMOVED_BG_SGR = "48;2;92;43;49"
_DIFF_CONTRAST_8BIT = "246"
_DIFF_CONTRAST_TRUECOLOR = ("170", "170", "170")


def _boost_foreground_contrast_for_diff(params: str) -> str:
    """Adjust low-contrast foreground SGR params for diff readability."""
    parts = [part for part in params.split(";") if part]
    if not parts:
        return f"0;38;5;{_DIFF_CONTRAST_8BIT}"

    boosted: list[str] = []
    index = 0
    saw_reset = False
    has_foreground_after_reset = False
    while index < len(parts):
        token = parts[index]
        if token in {"0", "00"}:
            boosted.append("0")
            saw_reset = True
            has_foreground_after_reset = False
            index += 1
            continue
        if token in {"38", "48"} and index + 1 < len(parts):
            mode = parts[index + 1]
            if mode == "5" and index + 2 < len(parts):
                color_token = parts[index + 2]
                if token == "38":
                    try:
                        color_index = int(color_token)
                    except ValueError:
                        color_index = -1
                    if 232 <= color_index <= 248:
                        boosted.extend(["38", "5", _DIFF_CONTRAST_8BIT])
                        has_foreground_after_reset = True
                        index += 3
                        continue
                    has_foreground_after_reset = True
                boosted.extend([token, "5", color_token])
                index += 3
                continue
            if mode == "2" and index + 4 < len(parts):
                red_token, green_token, blue_token = parts[index + 2 : index + 5]
                if token == "38":
                    try:
                        red, green, blue = map(int, (red_token, green_token, blue_token))
                    except ValueError:
                        red = green = blue = -1
                    if (
                        red >= 0
                        and green >= 0
                        and blue >= 0
                        and abs(red - green) <= 8
                        and abs(green - blue) <= 8
                        and max(red, green, blue) < 190
                    ):
                        boosted.extend(["38", "2", *_DIFF_CONTRAST_TRUECOLOR])
                        has_foreground_after_reset = True
                        index += 5
                        continue
                    has_foreground_after_reset = True
                boosted.extend([token, "2", red_token, green_token, blue_token])
                index += 5
                continue
        if token == "2":
            index += 1
            continue
        if token in {"30", "90", "39"}:
            boosted.extend(["38", "5", _DIFF_CONTRAST_8BIT])
            has_foreground_after_reset = True
            index += 1
            continue
        if token.isdigit():
            number = int(token)
            if 30 <= number <= 37 or 90 <= number <= 97:
                has_foreground_after_reset = True
        boosted.append(token)
        index += 1

    if saw_reset and not has_foreground_after_reset:
        boosted.extend(["38", "5", _DIFF_CONTRAST_8BIT])
    return ";".join(boosted)


def _apply_line_background(code_line: str, bg_sgr: str) -> str:
    """Apply a persistent background while preserving readable foregrounds."""

    def inject(match: re.Match[str]) -> str:
        params = _boost_foreground_contrast_for_diff(match.group(1))
        return f"\033[{params};{bg_sgr}m" if params else f"\033[{bg_sgr}m"

    persistent = _SGR_RE.sub(inject, code_line)
    return (
        f"\033[38;5;{_DIFF_CONTRAST_8BIT};{bg_sgr}m"
        f"{persistent}\033[K\033[0m"
    )


__all__ = [
    "_ADDED_BG_SGR",
    "_REMOVED_BG_SGR",
    "_apply_line_background",
    "_boost_foreground_contrast_for_diff",
]
