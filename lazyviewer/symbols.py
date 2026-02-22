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
    kind: str
    name: str
    line: int
    column: int
    label: str


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _language_for_path(path: Path) -> str | None:
    return LANGUAGE_BY_SUFFIX.get(path.suffix.lower())


@lru_cache(maxsize=32)
def _load_parser(language_name: str):
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
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _name_from_node(source_bytes: bytes, node, kind: str) -> str:
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
    if node_type in FUNCTION_NODE_TYPES:
        return "fn"
    if node_type in CLASS_NODE_TYPES:
        return "class"
    if node_type in IMPORT_NODE_TYPES:
        return "import"
    return None


def _format_label(kind: str, name: str, line: int) -> str:
    clean_name = name if len(name) <= 220 else (name[:217] + "...")
    return f"{kind:6} L{line + 1:>5}  {clean_name}"


def _collect_symbols_fallback(source: str, language_name: str, max_symbols: int) -> list[SymbolEntry]:
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
    try:
        resolved = path.resolve()
        stat = resolved.stat()
    except Exception:
        return None
    return (str(resolved), int(stat.st_mtime_ns), int(stat.st_size), int(max_symbols))


def _collect_symbols_cached(path: Path, max_symbols: int) -> tuple[list[SymbolEntry], str | None]:
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
    _SYMBOL_CONTEXT_CACHE.clear()


def collect_sticky_symbol_headers(path: Path, visible_start_line: int, max_headers: int = 3) -> list[str]:
    if max_headers <= 0:
        return []
    start_line = max(1, int(visible_start_line))
    if start_line <= 1:
        return []

    symbols, error = _collect_symbols_cached(path, max_symbols=4000)
    if error is not None or not symbols:
        return []

    candidates = [
        symbol
        for symbol in symbols
        if symbol.kind in {"class", "fn"} and (symbol.line + 1) < start_line
    ]
    if not candidates:
        return []

    selected = candidates[-max_headers:]
    headers: list[str] = []
    for symbol in selected:
        kind_label = "class" if symbol.kind == "class" else "fn"
        name = _normalize_whitespace(symbol.name)
        if len(name) > 180:
            name = name[:177] + "..."
        headers.append(f"{kind_label} {name}")
    return headers
