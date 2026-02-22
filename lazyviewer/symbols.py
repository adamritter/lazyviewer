from __future__ import annotations

import re
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
    try:
        from tree_sitter_languages import get_parser

        return get_parser(language_name), None
    except ModuleNotFoundError:
        pass
    except Exception as exc:
        return None, f"Failed to load Tree-sitter parser for {language_name}: {exc}"

    try:
        from tree_sitter_language_pack import get_parser

        return get_parser(language_name), None
    except ModuleNotFoundError:
        pass
    except Exception as exc:
        return None, f"Failed to load Tree-sitter parser for {language_name}: {exc}"

    return None, "Tree-sitter parser package not found. Install tree-sitter-languages."


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


def collect_symbols(path: Path, max_symbols: int = 2000) -> tuple[list[SymbolEntry], str | None]:
    target = path.resolve()
    if not target.is_file():
        return [], "Symbol outline is available for files only."

    language_name = _language_for_path(target)
    if language_name is None:
        suffix = target.suffix or "<no extension>"
        return [], f"No Tree-sitter grammar configured for {suffix}."

    parser, parser_error = _load_parser(language_name)
    if parser is None:
        return [], parser_error

    source = read_text(target)
    source_bytes = source.encode("utf-8", errors="replace")

    try:
        tree = parser.parse(source_bytes)
    except Exception as exc:
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
