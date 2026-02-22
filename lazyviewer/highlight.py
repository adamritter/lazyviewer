from __future__ import annotations

from pathlib import Path


def read_text(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_bytes().decode("utf-8", errors="replace")


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


def pygments_highlight(source: str, path: Path, style: str = "monokai") -> str | None:
    try:
        from pygments import highlight
        from pygments.formatters import TerminalFormatter
        from pygments.lexers import TextLexer, get_lexer_for_filename
        from pygments.styles import get_style_by_name
    except ImportError:
        return None

    try:
        get_style_by_name(style)
    except Exception:
        style = "monokai"

    try:
        lexer = get_lexer_for_filename(path.name, source)
    except Exception:
        lexer = TextLexer()

    formatter = TerminalFormatter(style=style)
    try:
        return highlight(source, lexer, formatter)
    except Exception:
        return None
