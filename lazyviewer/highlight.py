"""Source loading, sanitization, and syntax highlighting.

Tries Pygments first, then a lightweight tokenizer fallback.
Also neutralizes terminal control bytes to avoid unsafe preview side effects.
"""

from __future__ import annotations

import re
from pathlib import Path

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

_PYGMENTS_READY = False
_PYGMENTS_AVAILABLE = False
_PYGMENTS_HIGHLIGHT = None
_PYGMENTS_GET_LEXER_FOR_FILENAME = None
_PYGMENTS_TEXT_LEXER = None
_PYGMENTS_TERMINAL_FORMATTER = None
_PYGMENTS_GET_STYLE_BY_NAME = None
_PYGMENTS_FORMATTERS: dict[str, object] = {}
_PYGMENTS_VALID_STYLES: set[str] = set()
_PYGMENTS_INVALID_STYLES: set[str] = set()


def read_text(path: Path) -> str:
    """Read text using tolerant encoding fallback order.

    Attempts UTF-8, UTF-8 with BOM, then latin-1; as a final fallback decodes
    raw bytes with UTF-8 replacement semantics.
    """
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_bytes().decode("utf-8", errors="replace")


def sanitize_terminal_text(source: str) -> str:
    """Escape terminal control bytes to avoid side effects (bell, cursor moves, etc.)."""
    if _CONTROL_RE.search(source) is None:
        return source

    out: list[str] = []
    for ch in source:
        code = ord(ch)
        if ch in {"\n", "\r", "\t"}:
            out.append(ch)
            continue
        # C0 controls + DEL + C1 controls.
        if code < 32 or code == 127 or 0x80 <= code <= 0x9F:
            out.append(f"\\x{code:02x}")
            continue
        out.append(ch)
    return "".join(out)


def fallback_highlight(source: str) -> str:
    """Apply lightweight Python-token based ANSI highlighting.

    Used when Pygments is unavailable or fails to colorize.
    """
    try:
        import io
        import keyword
        import tokenize

        class Style:
            """ANSI style fragments used by fallback token coloring."""
            RESET = "\033[0m"
            BOLD = "\033[1m"
            BLUE = "\033[34m"
            GREEN = "\033[32m"
            CYAN = "\033[36m"
            YELLOW = "\033[33m"
            MAGENTA = "\033[35m"
            GRAY = "\033[90m"

        def style_for_token(tok_type: int, value: str) -> str:
            """Map tokenize token types/values to ANSI style fragments."""
            if tok_type == tokenize.STRING:
                return Style.GREEN
            if tok_type == tokenize.COMMENT:
                return Style.GRAY
            if tok_type == tokenize.NUMBER:
                return Style.CYAN
            if tok_type == tokenize.OP:
                return Style.YELLOW
            if tok_type == tokenize.NAME:
                if keyword.iskeyword(value):
                    return Style.BOLD + Style.BLUE
                if value in {"True", "False", "None"}:
                    return Style.MAGENTA
            return ""

        line_offsets = [0]
        for line in source.splitlines(keepends=True):
            line_offsets.append(line_offsets[-1] + len(line))

        def pos_to_offset(position: tuple[int, int]) -> int:
            """Convert tokenize ``(line, col)`` into absolute source offset."""
            line_no, col_no = position
            if line_no <= 0:
                return 0
            if (line_no - 1) >= len(line_offsets):
                return len(source)
            line_start = line_offsets[line_no - 1]
            return max(0, min(len(source), line_start + col_no))

        out: list[str] = []
        cursor = 0
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            tok_type = token.type
            if tok_type == tokenize.ENDMARKER:
                break

            start_offset = pos_to_offset(token.start)
            end_offset = pos_to_offset(token.end)

            if start_offset > cursor:
                out.append(source[cursor:start_offset])

            value = source[start_offset:end_offset]
            style = style_for_token(tok_type, token.string)
            if style and value:
                out.append(style + value + Style.RESET)
            else:
                out.append(value)
            cursor = max(cursor, end_offset)

        if cursor < len(source):
            out.append(source[cursor:])
        return "".join(out)
    except Exception:
        return source


def _ensure_pygments_loaded() -> bool:
    """Lazily import and cache Pygments callables.

    Returns whether Pygments is available in the runtime environment.
    """
    global _PYGMENTS_READY
    global _PYGMENTS_AVAILABLE
    global _PYGMENTS_HIGHLIGHT
    global _PYGMENTS_GET_LEXER_FOR_FILENAME
    global _PYGMENTS_TEXT_LEXER
    global _PYGMENTS_TERMINAL_FORMATTER
    global _PYGMENTS_GET_STYLE_BY_NAME

    if _PYGMENTS_READY:
        return _PYGMENTS_AVAILABLE

    _PYGMENTS_READY = True
    try:
        from pygments import highlight as pygments_highlight
        from pygments.formatters import TerminalFormatter
        from pygments.lexers import TextLexer, get_lexer_for_filename
        from pygments.styles import get_style_by_name
    except ImportError:
        _PYGMENTS_AVAILABLE = False
        return False

    _PYGMENTS_HIGHLIGHT = pygments_highlight
    _PYGMENTS_GET_LEXER_FOR_FILENAME = get_lexer_for_filename
    _PYGMENTS_TEXT_LEXER = TextLexer
    _PYGMENTS_TERMINAL_FORMATTER = TerminalFormatter
    _PYGMENTS_GET_STYLE_BY_NAME = get_style_by_name
    _PYGMENTS_AVAILABLE = True
    return True


def _normalize_style(style: str) -> str:
    """Validate/canonicalize requested style name with cache-backed checks."""
    if style in _PYGMENTS_VALID_STYLES:
        return style
    if style in _PYGMENTS_INVALID_STYLES:
        return "monokai"

    try:
        assert _PYGMENTS_GET_STYLE_BY_NAME is not None
        _PYGMENTS_GET_STYLE_BY_NAME(style)
        _PYGMENTS_VALID_STYLES.add(style)
        return style
    except Exception:
        _PYGMENTS_INVALID_STYLES.add(style)
        return "monokai"


def _formatter_for_style(style: str):
    """Return cached Pygments terminal formatter for style name."""
    formatter = _PYGMENTS_FORMATTERS.get(style)
    if formatter is not None:
        return formatter
    assert _PYGMENTS_TERMINAL_FORMATTER is not None
    formatter = _PYGMENTS_TERMINAL_FORMATTER(style=style)
    _PYGMENTS_FORMATTERS[style] = formatter
    return formatter


def pygments_highlight(source: str, path: Path, style: str = "monokai") -> str | None:
    """Highlight source with Pygments, returning ``None`` on any failure."""
    if not _ensure_pygments_loaded():
        return None

    style = _normalize_style(style)
    formatter = _formatter_for_style(style)

    try:
        assert _PYGMENTS_GET_LEXER_FOR_FILENAME is not None
        lexer = _PYGMENTS_GET_LEXER_FOR_FILENAME(path.name, source)
    except Exception:
        assert _PYGMENTS_TEXT_LEXER is not None
        lexer = _PYGMENTS_TEXT_LEXER()

    try:
        assert _PYGMENTS_HIGHLIGHT is not None
        return _PYGMENTS_HIGHLIGHT(source, lexer, formatter)
    except Exception:
        return None


def colorize_source(source: str, path: Path, style: str = "monokai") -> str:
    """Colorize source preferring Pygments, then tokenizer fallback.

    Returns original source if neither highlighter produces ANSI output.
    """
    rendered = pygments_highlight(source, path, style)
    if rendered and "\x1b[" in rendered:
        return rendered

    fallback = fallback_highlight(source)
    if "\x1b[" in fallback:
        return fallback

    return rendered or source
