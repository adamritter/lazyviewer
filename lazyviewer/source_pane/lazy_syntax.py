"""On-demand syntax lines for interactive previews.

Keep ordinary, complete text available for searching, line mapping and copying.
Only the renderer asks for styled lines. A suspended lexer preserves language
state across lines; cached lines never need to be lexed again when scrolling back.
"""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
import time
import threading

from . import syntax

SYNTAX_CACHE_ENTRIES = 32
SYNTAX_CACHE_CHARACTERS = 4_000_000
_CACHE: OrderedDict[tuple, LazyText] = OrderedDict()
_cache_characters = 0
_PREPARATION_LOCK = threading.RLock()
_WARMUP_STARTED = False
_WARMUP_ARMED = False
_WARMUP_FINISHED = False


@contextmanager
def defer_syntax_initialization():
    """Let the interactive window become usable before optional lexer imports."""
    global _WARMUP_ARMED
    previous = _WARMUP_ARMED
    _WARMUP_ARMED = True
    try:
        yield
    finally:
        _WARMUP_ARMED = previous


class HighlightingPending(Exception):
    """A background lexer preparation is in progress; retry next frame."""


def start_interactive_syntax_warmup() -> None:
    """Warm optional lexers after the first real frame without blocking input."""
    global _WARMUP_STARTED
    if _WARMUP_STARTED or _WARMUP_FINISHED:
        return
    _WARMUP_STARTED = True
    def worker():
        global _WARMUP_STARTED, _WARMUP_FINISHED
        # Leave time to present the frame and accept the first user action.
        time.sleep(0.05)
        if not _WARMUP_ARMED:
            _WARMUP_STARTED = False
            return
        try:
            prepare_interactive_syntax()
        except Exception:
            # Optional preparation must never prevent ordinary lazy coloring.
            pass
        finally:
            _WARMUP_FINISHED = True
    threading.Thread(target=worker, name="lazyviewer-syntax-warmup", daemon=True).start()


@lru_cache(maxsize=1024)
def _token_style(formatter, token):
    from pygments.console import ansiformat
    color = formatter._get_color(token)
    if not color:
        return "", ""
    return tuple(ansiformat(color, "\0").split("\0", 1))


@lru_cache(maxsize=128)
def _lexer_class(suffix: str, filename: str):
    from pygments.lexers import get_lexer_by_name
    common = {".py": "python", ".pyi": "python", ".c": "c", ".h": "c", ".cpp": "cpp",
              ".json": "json", ".jsonl": "json", ".js": "javascript", ".ts": "typescript",
              ".html": "html", ".xml": "xml", ".sh": "bash", ".yaml": "yaml", ".yml": "yaml",
              ".md": "markdown", ".rs": "rust", ".toml": "toml", ".s": "gas", ".asm": "nasm"}
    if suffix in common:
        return type(get_lexer_by_name(common[suffix]))
    if suffix in {".txt", ".log", ".csv", ".tsv", ".hex", ".map", ".lst"}:
        return syntax._PYGMENTS_TEXT_LEXER
    if not suffix and filename not in {"Makefile", "Dockerfile"}:
        return syntax._PYGMENTS_TEXT_LEXER
    try:
        return type(syntax._PYGMENTS_GET_LEXER_FOR_FILENAME(filename))
    except Exception:
        return syntax._PYGMENTS_TEXT_LEXER


def _lexer_for_path(path: Path):
    # Ordinary extensions share their lexer class; special basenames still use
    # Pygments' filename rules. Regex tables are compiled once per lexer class.
    suffix = path.suffix.lower()
    return _lexer_class(suffix, "file"+suffix if suffix else path.name)()


def prepare_interactive_syntax() -> None:
    """Prepare common lexers outside navigation and initial rendering."""
    global _WARMUP_FINISHED
    with _PREPARATION_LOCK:
        if syntax._ensure_pygments_loaded():
            for suffix in (".py", ".c", ".json", ".md", ".sh", ".html", ".cs", ".bat", ".vbs", ".ini", ".qml", ".asm", ".dtsi", ".css", ".js"):
                _lexer_for_path(Path("prepare"+suffix))
            syntax._formatter_for_style("monokai")
        _WARMUP_FINISHED = True


class LineHighlighter:
    """Consume a single token stream only as far as the requested source line."""

    def __init__(self, source: str, path: Path, style: str) -> None:
        self.source = source
        self.path = path
        self.style = style
        self.lines = source.splitlines(keepends=True) or [""]
        self.highlighted: list[str] = []
        self._stream = None
        self._formatter = None
        self._pending = None
        self._pending_offset = 0
        self._failed = False
        self._partial_chars = 0
        self._partial_rendered = ""

    def _start(self) -> None:
        # Do not wait on background regex compilation in a navigation frame.
        # Unlike a failed lexer, this is transient: the raw row remains visible
        # and the renderer will retry when preparation has finished.
        if _WARMUP_ARMED and not _WARMUP_FINISHED:
            raise HighlightingPending()
        if not _PREPARATION_LOCK.acquire(blocking=False):
            raise HighlightingPending()
        try:
            if not syntax._ensure_pygments_loaded():
                raise RuntimeError("Pygments unavailable")
            if not self.path.suffix and self.source.startswith("#!"):
                shebang = self.source.split("\n", 1)[0]
                language_suffix = ".py" if "python" in shebang else ".sh"
                lexer = _lexer_for_path(Path("script" + language_suffix))
            else:
                lexer = _lexer_for_path(self.path)
            # Unprocessed tokens preserve the exact input (including leading blank
            # lines and missing final newline). Keeping the iterator alive preserves
            # regex state for multiline strings/comments and embedded languages.
            self._stream = iter(lexer.get_tokens_unprocessed(self.source))
            self._formatter = syntax._formatter_for_style(syntax._normalize_style(self.style))
        finally:
            _PREPARATION_LOCK.release()

    def _next_line(self, raw: str) -> str:
        if self._stream is None:
            self._start()
        tokens = []
        remaining = len(raw)
        while remaining:
            if self._pending is None:
                _, token, value = next(self._stream)
                if not value:
                    continue
                self._pending = (token, value)
                self._pending_offset = 0
            token, value = self._pending
            take = min(remaining, len(value)-self._pending_offset)
            part = value[self._pending_offset:self._pending_offset+take]
            tokens.append((token, part))
            remaining -= take
            self._pending_offset += take
            if self._pending_offset == len(value):
                self._pending = None
        # A nonstandard lexer must never alter text, line mapping, or clipboard
        # contents. Degrade to plain text if it changes/omits input characters.
        if "".join(value for _, value in tokens) != raw:
            raise ValueError("Lexer changed source text")
        # Token pieces are confined to a single logical line. Cache the ANSI
        # envelope instead of asking the formatter to resolve token ancestry
        # and split lines again for every token on every frame.
        out = []
        for token, value in tokens:
            prefix, suffix = _token_style(self._formatter, token)
            newline = value.endswith("\n")
            body = value[:-1] if newline else value
            out.append(prefix + body + suffix)
            if newline:
                out.append("\n")
        return "".join(out)

    def line(self, index: int) -> str:
        return self.region(index, None)

    def region(self, index: int, max_characters: int | None, deadline: float = float("inf")) -> str | None:
        """Advance only to the visible horizontal prefix of the requested line."""
        if not 0 <= index < len(self.lines):
            raise IndexError(index)
        if index < len(self.highlighted):
            return self.highlighted[index]
        if _WARMUP_ARMED and not _WARMUP_FINISHED:
            return None
        while len(self.highlighted) < index:
            if time.perf_counter() >= deadline:
                return None
            previous = len(self.highlighted)
            step = min(len(self.lines[previous]), self._partial_chars + 512)
            if self.region(previous, step, deadline) is None:
                return None
        raw = self.lines[index]
        end = len(raw) if max_characters is None else min(len(raw), max_characters)
        end = max(end, self._partial_chars)
        if end > self._partial_chars:
            part = raw[self._partial_chars:end]
            rendered = part
            if not self._failed and part:
                try:
                    rendered = self._next_line(part)
                except HighlightingPending:
                    return None
                except Exception:
                    self._failed = True
                    self._stream = None
                    self._pending = None
            self._partial_rendered += rendered
            self._partial_chars = end
        result = self._partial_rendered + raw[end:]
        if end == len(raw):
            self.highlighted.append(self._partial_rendered)
            self._partial_rendered = ""
            self._partial_chars = 0
        return result


class LazyText(str):
    """Complete plain/annotated text with a separate, demand-driven row renderer."""

    def __new__(cls, text: str, render_line: Callable[[int], str], render_region=None):
        instance = super().__new__(cls, text)
        instance.render_line = render_line
        instance.render_region = render_region
        return instance

    def screen_lines(self, width: int, wrap: bool = False) -> list[str]:
        from ..render.ansi import build_screen_lines
        return ViewportLines(build_screen_lines(str(self), width, wrap), self, width, wrap)


class ViewportLines(list):
    """Raw rows for model operations, explicitly styled rows for display only."""

    def __init__(self, rows: list[str], text: LazyText, width: int, wrap: bool):
        super().__init__(rows)
        self._text = text
        self._width = width
        self._wrap = wrap
        self._styled: OrderedDict[tuple[int, int], list[str]] = OrderedDict()
        self._positions: list[tuple[int, int]] = []
        self._remaining_budget = float("inf")
        self.needs_highlighting = False
        self._text_x = 0
        line, chunk = 0, 0
        for row in rows:
            self._positions.append((line, chunk))
            if not wrap or row.endswith(("\n", "\r")):
                line, chunk = line+1, 0
            else:
                chunk += 1

    def begin_frame(self, budget_seconds: float = 0.001, text_x: int = 0) -> None:
        self._text_x = text_x
        self._remaining_budget = budget_seconds
        self.needs_highlighting = False

    def highlighted_line(self, index: int) -> str:
        from ..render.ansi import build_screen_lines
        line, chunk = self._positions[index]
        limit = self._text_x + self._width * (chunk + 1) + 8
        key = (line, limit)
        if key not in self._styled:
            if self._remaining_budget <= 0:
                self.needs_highlighting = True
                return self[index]
            # Charge only work done here. Tree/layout rendering between calls
            # must not exhaust the syntax budget before a row gets any time.
            started = time.perf_counter()
            deadline = started + self._remaining_budget
            try:
                render_region = self._text.render_region
                rendered = (render_region(line, limit, deadline) if render_region is not None
                            else self._text.render_line(line))
                if rendered is None:
                    self.needs_highlighting = True
                    return self[index]
                self._styled[key] = build_screen_lines(rendered, self._width, self._wrap)
                while len(self._styled) > 128:
                    self._styled.popitem(last=False)
            finally:
                self._remaining_budget -= time.perf_counter() - started
        chunks = self._styled[key]
        return chunks[chunk] if chunk < len(chunks) else self[index]


def clear_syntax_cache() -> None:
    global _cache_characters
    _CACHE.clear()
    _cache_characters = 0


def lazy_source(source: str, path: Path, style: str) -> LazyText:
    """Reuse lexer progress for unchanged content; bound retained document text."""
    global _cache_characters
    path = path.resolve()
    # Comparing the freshly read source protects against metadata-preserving
    # edits too; content and style are the actual cache validity conditions.
    key = (str(path), style)
    cached = _CACHE.get(key)
    if cached is not None and cached == source:
        _CACHE.move_to_end(key)
        return cached
    if cached is not None:
        _cache_characters -= len(_CACHE.pop(key))
    highlighter = LineHighlighter(source, path, style)
    result = LazyText(source, highlighter.line, highlighter.region)
    if len(source) <= SYNTAX_CACHE_CHARACTERS:
        _CACHE[key] = result
        _cache_characters += len(source)
        while len(_CACHE) > SYNTAX_CACHE_ENTRIES or _cache_characters > SYNTAX_CACHE_CHARACTERS:
            _, old = _CACHE.popitem(last=False)
            _cache_characters -= len(old)
    return result
