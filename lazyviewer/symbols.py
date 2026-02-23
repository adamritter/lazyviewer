"""Symbol-outline extraction for source files.

Uses Tree-sitter when available and regex fallbacks otherwise.
Also provides sticky-header context with a small per-file cache.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .highlight import read_text

LANGUAGE_BY_SUFFIX: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".pyw": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".lua": "lua",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
}

FUNCTION_NODE_TYPES = {
    "function_definition",
    "function_declaration",
    "function_item",
    "method_definition",
    "method_declaration",
}
CLASS_NODE_TYPES = {
    "class_definition",
    "class_declaration",
    "class_specifier",
}
IMPORT_NODE_TYPES = {
    "import_statement",
    "import_from_statement",
    "import_declaration",
    "using_directive",
    "using_declaration",
}
IDENTIFIER_NODE_TYPES = {
    "identifier",
    "type_identifier",
    "property_identifier",
    "field_identifier",
    "namespace_identifier",
}

MISSING_PARSER_ERROR = "Tree-sitter parser package not found. Install tree-sitter-languages."
SYMBOL_CONTEXT_CACHE_MAX = 128

_FALLBACK_PATTERNS_BY_LANGUAGE: dict[str, tuple[tuple[str, re.Pattern[str]], ...]] = {
    "python": (
        ("class", re.compile(r"^\s*class\s+(?P<name>[A-Za-z_][\w]*)")),
        ("fn", re.compile(r"^\s*(?:async\s+)?def\s+(?P<name>[A-Za-z_][\w]*)")),
    ),
    "javascript": (
        ("class", re.compile(r"^\s*(?:export\s+)?class\s+(?P<name>[A-Za-z_$][\w$]*)")),
        (
            "fn",
            re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)"),
        ),
        (
            "fn",
            re.compile(
                r"^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>"
            ),
        ),
    ),
    "typescript": (
        ("class", re.compile(r"^\s*(?:export\s+)?class\s+(?P<name>[A-Za-z_$][\w$]*)")),
        (
            "fn",
            re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)"),
        ),
        (
            "fn",
            re.compile(
                r"^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>"
            ),
        ),
    ),
    "tsx": (
        ("class", re.compile(r"^\s*(?:export\s+)?class\s+(?P<name>[A-Za-z_$][\w$]*)")),
        (
            "fn",
            re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)"),
        ),
        (
            "fn",
            re.compile(
                r"^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>"
            ),
        ),
    ),
    "go": (
        ("class", re.compile(r"^\s*type\s+(?P<name>[A-Za-z_][\w]*)\s+(?:struct|interface)\b")),
        ("fn", re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(?P<name>[A-Za-z_][\w]*)\s*\(")),
    ),
    "rust": (
        ("class", re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+(?P<name>[A-Za-z_][\w]*)\b")),
        ("fn", re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(?P<name>[A-Za-z_][\w]*)\s*\(")),
    ),
    "java": (
        ("class", re.compile(r"^\s*(?:public\s+|private\s+|protected\s+)?class\s+(?P<name>[A-Za-z_][\w]*)\b")),
    ),
    "ruby": (
        ("class", re.compile(r"^\s*class\s+(?P<name>[A-Za-z_][\w:]*)")),
        ("fn", re.compile(r"^\s*def\s+(?P<name>[A-Za-z_][\w!?=]*)")),
    ),
    "php": (
        ("class", re.compile(r"^\s*(?:final\s+|abstract\s+)?class\s+(?P<name>[A-Za-z_][\w]*)")),
        ("fn", re.compile(r"^\s*(?:public|private|protected|static|final|abstract|\s)*function\s+(?P<name>[A-Za-z_][\w]*)")),
    ),
    "swift": (
        ("class", re.compile(r"^\s*(?:public\s+|private\s+|internal\s+|open\s+)?class\s+(?P<name>[A-Za-z_][\w]*)")),
        ("fn", re.compile(r"^\s*(?:public\s+|private\s+|internal\s+|open\s+)?func\s+(?P<name>[A-Za-z_][\w]*)")),
    ),
    "kotlin": (
        ("class", re.compile(r"^\s*(?:public\s+|private\s+|internal\s+|open\s+)?class\s+(?P<name>[A-Za-z_][\w]*)")),
        ("fn", re.compile(r"^\s*(?:public\s+|private\s+|internal\s+|open\s+|suspend\s+)*fun\s+(?P<name>[A-Za-z_][\w]*)")),
    ),
    "scala": (
        ("class", re.compile(r"^\s*(?:case\s+)?class\s+(?P<name>[A-Za-z_][\w]*)")),
        ("fn", re.compile(r"^\s*def\s+(?P<name>[A-Za-z_][\w]*)")),
    ),
    "lua": (
        ("fn", re.compile(r"^\s*function\s+(?P<name>[A-Za-z_][\w\.:]*)")),
    ),
    "bash": (
        ("fn", re.compile(r"^\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{")),
        ("fn", re.compile(r"^\s*function\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b")),
    ),
}

_GENERIC_FALLBACK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("class", re.compile(r"^\s*(?:export\s+)?class\s+(?P<name>[A-Za-z_][\w$]*)")),
    ("class", re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+(?P<name>[A-Za-z_][\w]*)\b")),
    ("fn", re.compile(r"^\s*(?:async\s+)?def\s+(?P<name>[A-Za-z_][\w]*)")),
    ("fn", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)")),
    ("fn", re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(?P<name>[A-Za-z_][\w]*)\s*\(")),
    ("fn", re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(?P<name>[A-Za-z_][\w]*)\s*\(")),
)

_SYMBOL_CONTEXT_CACHE: OrderedDict[
    tuple[str, int, int, int],
    tuple[tuple[SymbolEntry, ...], str | None],
] = OrderedDict()


@dataclass(frozen=True)
class SymbolEntry:
    """Normalized symbol record used by picker and sticky-header features."""

    kind: str
    name: str
    line: int
    column: int
    label: str


def _normalize_whitespace(text: str) -> str:
    """Collapse internal whitespace to single spaces for stable labels."""
    return re.sub(r"\s+", " ", text).strip()


def _language_for_path(path: Path) -> str | None:
    """Map file suffix to configured Tree-sitter language key."""
    return LANGUAGE_BY_SUFFIX.get(path.suffix.lower())


@lru_cache(maxsize=32)
def _load_parser(language_name: str):
    """Load a Tree-sitter parser using supported provider packages.

    Tries ``tree_sitter_languages`` first, then ``tree_sitter_language_pack``.
    Returns ``(parser, error_message)``.
    """
    errors: list[str] = []

    try:
        from tree_sitter_languages import get_parser

        return get_parser(language_name), None
    except ModuleNotFoundError:
        pass
    except Exception as exc:
        errors.append(f"Failed to load Tree-sitter parser for {language_name}: {exc}")

    try:
        from tree_sitter_language_pack import get_parser

        return get_parser(language_name), None
    except ModuleNotFoundError:
        pass
    except Exception as exc:
        errors.append(f"Failed to load Tree-sitter parser for {language_name}: {exc}")

    if errors:
        return None, errors[0]

    return None, MISSING_PARSER_ERROR


def _node_text(source_bytes: bytes, node) -> str:
    """Decode source slice covered by a Tree-sitter node."""
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _name_from_node(source_bytes: bytes, node, kind: str) -> str:
    """Extract display name for a symbol/import node."""
    if kind == "import":
        return _normalize_whitespace(_node_text(source_bytes, node))

    for field_name in ("name", "declarator"):
        child = node.child_by_field_name(field_name)
        if child is None:
            continue
        nested = child.child_by_field_name("name")
        if nested is not None:
            return _normalize_whitespace(_node_text(source_bytes, nested))
        return _normalize_whitespace(_node_text(source_bytes, child))

    for child in node.named_children:
        if child.type in IDENTIFIER_NODE_TYPES:
            return _normalize_whitespace(_node_text(source_bytes, child))

    return _normalize_whitespace(_node_text(source_bytes, node))


def _symbol_kind(node_type: str) -> str | None:
    """Map Tree-sitter node type to normalized symbol kind."""
    if node_type in FUNCTION_NODE_TYPES:
        return "fn"
    if node_type in CLASS_NODE_TYPES:
        return "class"
    if node_type in IMPORT_NODE_TYPES:
        return "import"
    return None


def _format_label(kind: str, name: str, line: int) -> str:
    """Build fixed-width picker label for a symbol entry."""
    clean_name = name if len(name) <= 220 else (name[:217] + "...")
    return f"{kind:6} L{line + 1:>5}  {clean_name}"


def _collect_symbols_fallback(source: str, language_name: str, max_symbols: int) -> list[SymbolEntry]:
    """Collect symbols via language-specific regex patterns."""
    patterns = _FALLBACK_PATTERNS_BY_LANGUAGE.get(language_name, _GENERIC_FALLBACK_PATTERNS)
    symbols: list[SymbolEntry] = []
    seen: set[tuple[int, int, str, str]] = set()
    for line_idx, line in enumerate(source.splitlines()):
        for kind, pattern in patterns:
            match = pattern.match(line)
            if match is None:
                continue
            name = _normalize_whitespace(match.group("name"))
            if not name:
                continue
            column = int(match.start("name"))
            key = (line_idx, column, kind, name.casefold())
            if key in seen:
                break
            seen.add(key)
            symbols.append(
                SymbolEntry(
                    kind=kind,
                    name=name,
                    line=line_idx,
                    column=column,
                    label=_format_label(kind, name, line_idx),
                )
            )
            if len(symbols) >= max_symbols:
                symbols.sort(key=lambda item: (item.line, item.column, item.kind, item.name.casefold()))
                return symbols
            break

    symbols.sort(key=lambda item: (item.line, item.column, item.kind, item.name.casefold()))
    return symbols


def collect_symbols(path: Path, max_symbols: int = 2000) -> tuple[list[SymbolEntry], str | None]:
    """Collect outline symbols for a source file.

    Returns ``(symbols, error_message)``. When parser loading/parsing fails, a
    regex fallback is used before surfacing an error.
    """
    target = path.resolve()
    if not target.is_file():
        return [], "Symbol outline is available for files only."

    language_name = _language_for_path(target)
    if language_name is None:
        suffix = target.suffix or "<no extension>"
        return [], f"No Tree-sitter grammar configured for {suffix}."

    parser, parser_error = _load_parser(language_name)

    try:
        source = read_text(target)
    except Exception as exc:
        return [], f"Failed to read source for symbols: {exc}"
    source_bytes = source.encode("utf-8", errors="replace")

    if parser is None:
        fallback_symbols = _collect_symbols_fallback(source, language_name, max_symbols=max_symbols)
        if fallback_symbols:
            return fallback_symbols, None
        return [], (parser_error or MISSING_PARSER_ERROR)

    try:
        tree = parser.parse(source_bytes)
    except Exception as exc:
        fallback_symbols = _collect_symbols_fallback(source, language_name, max_symbols=max_symbols)
        if fallback_symbols:
            return fallback_symbols, None
        return [], f"Tree-sitter parse failed: {exc}"

    symbols: list[SymbolEntry] = []

    def walk(node) -> None:
        """Depth-first traversal collecting symbol-bearing nodes."""
        if len(symbols) >= max_symbols:
            return

        if node.type in {"decorated_definition", "decorated_declaration"}:
            definition = node.child_by_field_name("definition")
            if definition is not None:
                walk(definition)
                return

        kind = _symbol_kind(node.type)
        if kind is not None:
            name = _name_from_node(source_bytes, node, kind)
            line, column = node.start_point
            symbols.append(
                SymbolEntry(
                    kind=kind,
                    name=name,
                    line=int(line),
                    column=int(column),
                    label=_format_label(kind, name, int(line)),
                )
            )

        for child in node.named_children:
            walk(child)

    walk(tree.root_node)
    symbols.sort(key=lambda item: (item.line, item.column, item.kind, item.name.casefold()))
    return symbols, None


def _symbol_context_cache_key(path: Path, max_symbols: int) -> tuple[str, int, int, int] | None:
    """Build cache key from resolved path, mtime, size, and symbol limit."""
    try:
        resolved = path.resolve()
        stat = resolved.stat()
    except Exception:
        return None
    return (str(resolved), int(stat.st_mtime_ns), int(stat.st_size), int(max_symbols))


def _collect_symbols_cached(path: Path, max_symbols: int) -> tuple[list[SymbolEntry], str | None]:
    """Collect symbols with small LRU cache keyed by file identity+mtime."""
    cache_key = _symbol_context_cache_key(path, max_symbols)
    if cache_key is not None:
        cached = _SYMBOL_CONTEXT_CACHE.get(cache_key)
        if cached is not None:
            _SYMBOL_CONTEXT_CACHE.move_to_end(cache_key)
            cached_symbols, cached_error = cached
            return list(cached_symbols), cached_error

    symbols, error = collect_symbols(path, max_symbols=max_symbols)

    if cache_key is not None:
        _SYMBOL_CONTEXT_CACHE[cache_key] = (tuple(symbols), error)
        _SYMBOL_CONTEXT_CACHE.move_to_end(cache_key)
        while len(_SYMBOL_CONTEXT_CACHE) > SYMBOL_CONTEXT_CACHE_MAX:
            _SYMBOL_CONTEXT_CACHE.popitem(last=False)

    return symbols, error


def clear_symbol_context_cache() -> None:
    """Drop cached symbol and sticky-context entries."""
    _SYMBOL_CONTEXT_CACHE.clear()


def _leading_indent_columns(text: str) -> int:
    """Return leading indentation width where tabs count as four columns."""
    count = 0
    for ch in text:
        if ch == " ":
            count += 1
            continue
        if ch == "\t":
            count += 4
            continue
        break
    return count


def _enclosing_sticky_symbol_chain(
    candidates: list[SymbolEntry],
    source_lines: list[str],
) -> list[SymbolEntry]:
    """Reduce candidates to indentation-based enclosing symbol stack."""
    if not candidates:
        return []

    if not source_lines:
        return candidates

    stack: list[tuple[SymbolEntry, int]] = []
    for symbol in candidates:
        line_index = symbol.line
        if 0 <= line_index < len(source_lines):
            indent = _leading_indent_columns(source_lines[line_index])
        else:
            indent = 0

        while stack and indent <= stack[-1][1]:
            stack.pop()
        stack.append((symbol, indent))

    return [symbol for symbol, _indent in stack]


def collect_sticky_symbol_headers(
    path: Path,
    visible_start_line: int,
    max_headers: int = 1,
) -> list[SymbolEntry]:
    """Return enclosing class/function headers above the visible top line."""
    if max_headers <= 0:
        return []
    start_line = max(1, int(visible_start_line))
    if start_line <= 1:
        return []

    symbols, error = _collect_symbols_cached(path, max_symbols=4000)
    if error is not None or not symbols:
        return []

    all_candidates = [
        symbol
        for symbol in symbols
        if symbol.kind in {"class", "fn"} and (symbol.line + 1) < start_line
    ]
    if not all_candidates:
        return []

    try:
        source_lines = read_text(path).splitlines()
    except Exception:
        source_lines = []

    chain = _enclosing_sticky_symbol_chain(all_candidates, source_lines)
    if not chain:
        return []

    if max_headers >= len(chain):
        return chain
    return chain[-max_headers:]



def next_symbol_start_line(path: Path, after_line: int) -> int | None:
    """Return the next class/function declaration line (1-based) after ``after_line``."""
    start_line = max(1, int(after_line))
    symbols, error = _collect_symbols_cached(path, max_symbols=4000)
    if error is not None or not symbols:
        return None

    for symbol in symbols:
        symbol_line = symbol.line + 1
        if symbol.kind in {"class", "fn"} and symbol_line > start_line:
            return symbol_line
    return None
