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
    try:
        import io
        import keyword
        import tokenize

        class Style:
            RESET = "\033[0m"
            BOLD = "\033[1m"
            BLUE = "\033[34m"
            GREEN = "\033[32m"
            CYAN = "\033[36m"
            YELLOW = "\033[33m"
            MAGENTA = "\033[35m"
            GRAY = "\033[90m"

        def style_for_token(tok_type: int, value: str) -> str:
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

        out = []
        for tok_type, value, _, _, _ in tokenize.generate_tokens(io.StringIO(source).readline):
            style = style_for_token(tok_type, value)
            out.append(style + value + Style.RESET if style else value)
        return "".join(out)
    except Exception:
        return source


def _ensure_pygments_loaded() -> bool:
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
    formatter = _PYGMENTS_FORMATTERS.get(style)
    if formatter is not None:
        return formatter
    assert _PYGMENTS_TERMINAL_FORMATTER is not None
    formatter = _PYGMENTS_TERMINAL_FORMATTER(style=style)
    _PYGMENTS_FORMATTERS[style] = formatter
    return formatter


def pygments_highlight(source: str, path: Path, style: str = "monokai") -> str | None:
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
    rendered = pygments_highlight(source, path, style)
    if rendered and "\x1b[" in rendered:
        return rendered

    fallback = fallback_highlight(source)
    if "\x1b[" in fallback:
        return fallback

    return rendered or source
