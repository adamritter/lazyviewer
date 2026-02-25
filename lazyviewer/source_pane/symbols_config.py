"""Language/grammar configuration for symbol extraction."""

from __future__ import annotations

import re

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

MISSING_PARSER_ERROR = (
    "Tree-sitter parser package not found. Install tree-sitter-languages or tree-sitter-language-pack."
)
SYMBOL_CONTEXT_CACHE_MAX = 128

FALLBACK_PATTERNS_BY_LANGUAGE: dict[str, tuple[tuple[str, re.Pattern[str]], ...]] = {
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
        (
            "fn",
            re.compile(r"^\s*(?:public|private|protected|static|final|abstract|\s)*function\s+(?P<name>[A-Za-z_][\w]*)"),
        ),
    ),
    "swift": (
        ("class", re.compile(r"^\s*(?:public\s+|private\s+|internal\s+|open\s+)?class\s+(?P<name>[A-Za-z_][\w]*)")),
        ("fn", re.compile(r"^\s*(?:public\s+|private\s+|internal\s+|open\s+)?func\s+(?P<name>[A-Za-z_][\w]*)")),
    ),
    "kotlin": (
        ("class", re.compile(r"^\s*(?:public\s+|private\s+|internal\s+|open\s+)?class\s+(?P<name>[A-Za-z_][\w]*)")),
        (
            "fn",
            re.compile(r"^\s*(?:public\s+|private\s+|internal\s+|open\s+|suspend\s+)*fun\s+(?P<name>[A-Za-z_][\w]*)"),
        ),
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

GENERIC_FALLBACK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("class", re.compile(r"^\s*(?:export\s+)?class\s+(?P<name>[A-Za-z_][\w$]*)")),
    ("class", re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+(?P<name>[A-Za-z_][\w]*)\b")),
    ("fn", re.compile(r"^\s*(?:async\s+)?def\s+(?P<name>[A-Za-z_][\w]*)")),
    ("fn", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)")),
    ("fn", re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(?P<name>[A-Za-z_][\w]*)\s*\(")),
    ("fn", re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(?P<name>[A-Za-z_][\w]*)\s*\(")),
)
