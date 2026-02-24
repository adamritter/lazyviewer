"""Apply search-hit and selection highlighting to ANSI-rendered source rows.

Functions here preserve existing ANSI style sequences while layering additional
emphasis for query matches and source-selection ranges.
"""

from __future__ import annotations

from ..render.ansi import ANSI_ESCAPE_RE, clip_ansi_line, slice_ansi_line

SOURCE_SELECTION_BG_SGR = "48;2;58;92;188"
_DIFF_ADDED_BG_SGR = "48;2;36;74;52"
_DIFF_REMOVED_BG_SGR = "48;2;92;43;49"
_DIFF_CONTRAST_FG_SGR = "38;5;246"
_DIFF_TRUECOLOR_CONTRAST_FG_SGR = "38;2;220;220;220"
_DIFF_ADDED_BG_RGB = (36, 74, 52)
_DIFF_REMOVED_BG_RGB = (92, 43, 49)
_DIFF_MIN_CONTRAST_RATIO = 3.0
_DIFF_EDGE_CONTRAST_CHAR_COUNT = 2
_XTERM_16_RGB: tuple[tuple[int, int, int], ...] = (
    (0, 0, 0),
    (128, 0, 0),
    (0, 128, 0),
    (128, 128, 0),
    (0, 0, 128),
    (128, 0, 128),
    (0, 128, 128),
    (192, 192, 192),
    (128, 128, 128),
    (255, 0, 0),
    (0, 255, 0),
    (255, 255, 0),
    (0, 0, 255),
    (255, 0, 255),
    (0, 255, 255),
    (255, 255, 255),
)
_DIFF_STANDARD_FG_BOOST_MAP: dict[str, str] = {
    "30": "38;5;246",
    "31": "38;5;210",
    "32": "38;5;120",
    "33": "38;5;229",
    "34": "38;5;117",
    "35": "38;5;183",
    "36": "38;5;159",
    "90": "38;5;246",
    "91": "38;5;210",
    "92": "38;5;120",
    "93": "38;5;229",
    "94": "38;5;117",
    "95": "38;5;183",
    "96": "38;5;159",
    "default": "38;5;246",
}


def highlight_ansi_substrings(
    text: str,
    query: str,
    current_column: int | None = None,
    has_current_target: bool = False,
) -> str:
    """Highlight case-insensitive query matches in ANSI text.

    When ``has_current_target`` is true, the current hit gets stronger emphasis
    while other hits are still highlighted.
    """
    if not text or not query:
        return text

    visible_chars: list[str] = []
    visible_start: list[int] = []
    visible_end: list[int] = []

    idx = 0
    text_len = len(text)
    while idx < text_len:
        if text[idx] == "\x1b":
            match = ANSI_ESCAPE_RE.match(text, idx)
            if match:
                idx = match.end()
                continue
        visible_start.append(idx)
        visible_chars.append(text[idx])
        idx += 1
        visible_end.append(idx)

    if not visible_chars:
        return text

    visible_text = "".join(visible_chars)
    folded_text = visible_text.casefold()
    folded_query = query.casefold()
    if not folded_query:
        return text

    spans: list[tuple[int, int]] = []
    cursor = 0
    while True:
        found = folded_text.find(folded_query, cursor)
        if found < 0:
            break
        end = found + len(query)
        spans.append((found, end))
        cursor = end

    if not spans:
        return text

    current_idx: int | None = None
    if has_current_target and current_column is not None and spans:
        target = max(0, current_column - 1)
        for span_idx, (start_vis, end_vis) in enumerate(spans):
            if start_vis <= target < end_vis:
                current_idx = span_idx
                break
        if current_idx is None:
            for span_idx, (start_vis, _end_vis) in enumerate(spans):
                if start_vis >= target:
                    current_idx = span_idx
                    break
        if current_idx is None:
            current_idx = len(spans) - 1

    primary_start = "\033[7;1m"
    primary_end = "\033[27;22m"
    secondary_start = "\033[1m"
    secondary_end = "\033[22m"

    out: list[str] = []
    raw_cursor = 0
    for span_idx, (start_vis, end_vis) in enumerate(spans):
        if start_vis >= len(visible_start) or end_vis <= 0:
            continue
        start_raw = visible_start[start_vis]
        end_raw = visible_end[min(len(visible_end) - 1, end_vis - 1)]
        out.append(text[raw_cursor:start_raw])
        if has_current_target:
            if current_idx is not None and span_idx == current_idx:
                out.append(primary_start)
            else:
                out.append(secondary_start)
        else:
            out.append(primary_start)
        out.append(text[start_raw:end_raw])
        if has_current_target:
            if current_idx is not None and span_idx == current_idx:
                out.append(primary_end)
            else:
                out.append(secondary_end)
        else:
            out.append(primary_end)
        raw_cursor = end_raw
    out.append(text[raw_cursor:])
    return "".join(out)


def _highlight_segment_with_background(text: str) -> str:
    """Apply selection background while preserving existing ANSI attributes."""
    if not text:
        return text
    out: list[str] = [f"\033[{SOURCE_SELECTION_BG_SGR}m"]
    idx = 0
    while idx < len(text):
        if text[idx] == "\x1b":
            match = ANSI_ESCAPE_RE.match(text, idx)
            if match is not None:
                seq = match.group(0)
                if seq.endswith("m"):
                    params = seq[2:-1]
                    if params:
                        out.append(f"\033[{params};{SOURCE_SELECTION_BG_SGR}m")
                    else:
                        out.append(f"\033[{SOURCE_SELECTION_BG_SGR}m")
                else:
                    out.append(seq)
                idx = match.end()
                continue
        out.append(text[idx])
        idx += 1
    out.append("\033[49m")
    return "".join(out)


def highlight_ansi_column_range(text: str, start_col: int, end_col: int) -> str:
    """Highlight an ANSI text segment spanning display columns ``[start_col,end_col)``."""
    if not text:
        return text
    if end_col <= start_col:
        return text

    visible_start: list[int] = []
    visible_end: list[int] = []

    idx = 0
    text_len = len(text)
    while idx < text_len:
        if text[idx] == "\x1b":
            match = ANSI_ESCAPE_RE.match(text, idx)
            if match:
                idx = match.end()
                continue
        visible_start.append(idx)
        idx += 1
        visible_end.append(idx)

    if not visible_start:
        return text

    start_idx = max(0, start_col)
    end_idx = min(len(visible_start), end_col)
    if end_idx <= start_idx:
        return text

    raw_start = visible_start[start_idx]
    raw_end = visible_end[end_idx - 1]
    return (
        text[:raw_start]
        + _highlight_segment_with_background(text[raw_start:raw_end])
        + text[raw_end:]
    )


def normalized_selection_range(
    anchor: tuple[int, int] | None,
    focus: tuple[int, int] | None,
) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """Normalize nullable selection endpoints into ordered ``(anchor, focus)`` pair."""
    if anchor is None and focus is None:
        return None
    if anchor is None:
        anchor = focus
    if focus is None:
        focus = anchor
    assert anchor is not None and focus is not None
    if focus < anchor:
        anchor, focus = focus, anchor
    return anchor, focus


def selection_span_for_rendered_line(
    line_idx: int,
    line_plain_length: int,
    selection_range: tuple[tuple[int, int], tuple[int, int]] | None,
    viewport_start_col: int,
    viewport_end_col: int,
) -> tuple[int, int] | None:
    """Project absolute selection range onto one rendered line's viewport columns."""
    if selection_range is None:
        return None

    (start_line, start_col), (end_line, end_col) = selection_range
    if line_idx < start_line or line_idx > end_line:
        return None

    if start_line == end_line:
        abs_start = max(0, min(start_col, line_plain_length))
        abs_end = max(abs_start, min(end_col, line_plain_length))
    elif line_idx == start_line:
        abs_start = max(0, min(start_col, line_plain_length))
        abs_end = line_plain_length
    elif line_idx == end_line:
        abs_start = 0
        abs_end = max(0, min(end_col, line_plain_length))
    else:
        abs_start = 0
        abs_end = line_plain_length

    if abs_end <= abs_start:
        if line_plain_length <= abs_start:
            return None
        abs_end = abs_start + 1

    visible_start = max(abs_start, viewport_start_col)
    visible_end = min(abs_end, viewport_end_col)
    if visible_end <= visible_start:
        return None

    return visible_start - viewport_start_col, visible_end - viewport_start_col


def _is_low_contrast_diff_fg(foreground: str) -> bool:
    def _srgb_to_linear(channel: int) -> float:
        value = max(0, min(channel, 255)) / 255.0
        if value <= 0.04045:
            return value / 12.92
        return ((value + 0.055) / 1.055) ** 2.4

    def _relative_luminance(rgb: tuple[int, int, int]) -> float:
        red, green, blue = rgb
        return (
            0.2126 * _srgb_to_linear(red)
            + 0.7152 * _srgb_to_linear(green)
            + 0.0722 * _srgb_to_linear(blue)
        )

    def _contrast_ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
        a_lum = _relative_luminance(a)
        b_lum = _relative_luminance(b)
        light = max(a_lum, b_lum)
        dark = min(a_lum, b_lum)
        return (light + 0.05) / (dark + 0.05)

    def _xterm_256_rgb(color_index: int) -> tuple[int, int, int] | None:
        if color_index < 0 or color_index > 255:
            return None
        if color_index <= 15:
            return _XTERM_16_RGB[color_index]
        if 16 <= color_index <= 231:
            cube = color_index - 16
            red_idx = cube // 36
            green_idx = (cube % 36) // 6
            blue_idx = cube % 6
            steps = (0, 95, 135, 175, 215, 255)
            return (steps[red_idx], steps[green_idx], steps[blue_idx])
        gray = 8 + (color_index - 232) * 10
        return (gray, gray, gray)

    foreground_rgb: tuple[int, int, int] | None = None
    if foreground == "default":
        foreground_rgb = (0, 0, 0)
    elif foreground.isdigit():
        token = int(foreground)
        if 30 <= token <= 37:
            foreground_rgb = _XTERM_16_RGB[token - 30]
        elif 90 <= token <= 97:
            foreground_rgb = _XTERM_16_RGB[token - 90 + 8]
    if foreground.startswith("38;5;"):
        try:
            color_index = int(foreground.split(";")[2])
        except (IndexError, ValueError):
            return False
        foreground_rgb = _xterm_256_rgb(color_index)
    elif foreground.startswith("38;2;"):
        parts = foreground.split(";")
        if len(parts) != 5:
            return False
        try:
            red = int(parts[2])
            green = int(parts[3])
            blue = int(parts[4])
        except ValueError:
            return False
        foreground_rgb = (red, green, blue)

    if foreground_rgb is None:
        return False
    ratio = min(
        _contrast_ratio(foreground_rgb, _DIFF_ADDED_BG_RGB),
        _contrast_ratio(foreground_rgb, _DIFF_REMOVED_BG_RGB),
    )
    return ratio < _DIFF_MIN_CONTRAST_RATIO


def _requires_diff_edge_boost(foreground: str) -> bool:
    """Return whether foreground is likely a reset/dim color requiring edge repair."""
    if foreground in {"default", "30", "90"}:
        return True

    if foreground.startswith("38;5;"):
        try:
            color_index = int(foreground.split(";")[2])
        except (IndexError, ValueError):
            return False
        return 232 <= color_index <= 245

    if foreground.startswith("38;2;"):
        parts = foreground.split(";")
        if len(parts) != 5:
            return False
        try:
            red = int(parts[2])
            green = int(parts[3])
            blue = int(parts[4])
        except ValueError:
            return False
        return (
            abs(red - green) <= 12
            and abs(green - blue) <= 12
            and max(red, green, blue) < 150
        )

    return False


def _ensure_diff_trailing_char_contrast(text: str) -> str:
    """Ensure trailing visible diff characters keep readable foreground contrast."""
    if not text:
        return text
    if _DIFF_ADDED_BG_SGR not in text and _DIFF_REMOVED_BG_SGR not in text:
        return text

    foreground = "default"
    background = "default"
    idx = 0
    visible_nonspace_chars: list[tuple[int, str, str]] = []
    while idx < len(text):
        if text[idx] == "\x1b":
            match = ANSI_ESCAPE_RE.match(text, idx)
            if match:
                seq = match.group(0)
                if seq.endswith("m"):
                    params = seq[2:-1]
                    parts = [part for part in params.split(";") if part]
                    if not parts:
                        parts = ["0"]
                    part_idx = 0
                    while part_idx < len(parts):
                        part = parts[part_idx]
                        try:
                            token = int(part)
                        except ValueError:
                            part_idx += 1
                            continue
                        if token == 0:
                            foreground = "default"
                            background = "default"
                        elif token == 39:
                            foreground = "default"
                        elif token == 49:
                            background = "default"
                        elif 30 <= token <= 37 or 90 <= token <= 97:
                            foreground = str(token)
                        elif 40 <= token <= 47 or 100 <= token <= 107:
                            background = str(token)
                        elif token in {38, 48} and part_idx + 1 < len(parts):
                            mode = parts[part_idx + 1]
                            if mode == "5" and part_idx + 2 < len(parts):
                                if token == 38:
                                    foreground = f"38;5;{parts[part_idx + 2]}"
                                else:
                                    background = f"48;5;{parts[part_idx + 2]}"
                                part_idx += 2
                            elif mode == "2" and part_idx + 4 < len(parts):
                                if token == 38:
                                    foreground = ";".join(["38", "2", *parts[part_idx + 2 : part_idx + 5]])
                                else:
                                    background = ";".join(["48", "2", *parts[part_idx + 2 : part_idx + 5]])
                                part_idx += 4
                        part_idx += 1
                idx = match.end()
                continue

        char = text[idx]
        if char not in "\r\n" and not char.isspace():
            visible_nonspace_chars.append((idx, foreground, background))
        idx += 1

    if not visible_nonspace_chars:
        return text

    candidates: list[tuple[int, str, str]] = []
    for char_index, char_fg, char_bg in reversed(visible_nonspace_chars):
        if char_bg not in {_DIFF_ADDED_BG_SGR, _DIFF_REMOVED_BG_SGR}:
            break
        candidates.append((char_index, char_fg, char_bg))
        if len(candidates) >= _DIFF_EDGE_CONTRAST_CHAR_COUNT:
            break

    if not candidates:
        return text

    candidates.reverse()

    insertion_specs: list[tuple[int, str, str, str]] = []
    for char_index, char_fg, char_bg in candidates:
        if not _requires_diff_edge_boost(char_fg):
            continue
        if not _is_low_contrast_diff_fg(char_fg):
            continue
        boosted_fg = _DIFF_STANDARD_FG_BOOST_MAP.get(char_fg, _DIFF_CONTRAST_FG_SGR)
        if char_fg.startswith("38;2;"):
            boosted_fg = _DIFF_TRUECOLOR_CONTRAST_FG_SGR
        insertion_specs.append((char_index, boosted_fg, char_bg, char_fg))

    if not insertion_specs:
        return text

    # If the edge run shares the same foreground/background, one insertion
    # before the first edge character preserves contiguous token coloring.
    if len(insertion_specs) > 1:
        same_bg = all(spec[2] == insertion_specs[0][2] for spec in insertion_specs)
        same_fg = all(spec[3] == insertion_specs[0][3] for spec in insertion_specs)
        same_boosted_fg = all(spec[1] == insertion_specs[0][1] for spec in insertion_specs)
        first_index = insertion_specs[0][0]
        last_index = insertion_specs[-1][0]
        has_intervening_escape = "\x1b" in text[first_index:last_index]
        if same_bg and same_fg and same_boosted_fg and not has_intervening_escape:
            return (
                text[:first_index]
                + f"\033[{insertion_specs[0][1]};{insertion_specs[0][2]}m"
                + text[first_index:]
            )

    out = text
    for char_index, boosted_fg, char_bg, _char_fg in sorted(insertion_specs, key=lambda item: item[0], reverse=True):
        out = out[:char_index] + f"\033[{boosted_fg};{char_bg}m" + out[char_index:]
    return out


def rendered_preview_row(
    text_lines: list[str],
    text_idx: int,
    width: int,
    wrap_text: bool,
    text_x: int,
    text_search_query: str,
    text_search_current_line: int,
    text_search_current_column: int,
    has_current_text_hit: bool,
    selection_range: tuple[tuple[int, int], tuple[int, int]] | None,
    *,
    preview_is_git_diff: bool = False,
) -> str:
    """Render one source row with viewport clipping, search, and selection overlays."""
    if text_idx >= len(text_lines):
        return ""

    full_line = text_lines[text_idx].rstrip("\r\n")
    line_plain_length = len(ANSI_ESCAPE_RE.sub("", full_line))
    if wrap_text:
        text_raw = clip_ansi_line(full_line, width)
        viewport_start_col = 0
        viewport_end_col = width
    else:
        text_raw = slice_ansi_line(full_line, text_x, width)
        viewport_start_col = text_x
        viewport_end_col = text_x + width

    if preview_is_git_diff:
        text_raw = _ensure_diff_trailing_char_contrast(text_raw)

    current_col = text_search_current_column if text_idx + 1 == text_search_current_line else None
    text_raw = highlight_ansi_substrings(
        text_raw,
        text_search_query,
        current_column=current_col,
        has_current_target=has_current_text_hit,
    )
    selection_span = selection_span_for_rendered_line(
        text_idx,
        line_plain_length,
        selection_range,
        viewport_start_col,
        viewport_end_col,
    )
    if selection_span is not None:
        text_raw = highlight_ansi_column_range(
            text_raw,
            selection_span[0],
            selection_span[1],
        )
    return text_raw
