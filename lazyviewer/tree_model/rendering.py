"""Formatting helpers for tree/search-hit rows."""

from __future__ import annotations

from pathlib import Path

from ..git_status import format_git_status_badges
from .types import TreeEntry

TREE_SIZE_LABEL_MIN_BYTES = 10 * 1024


def file_color_for(path: Path) -> str:
    """Return ANSI color used for file names based on suffix."""
    suffix = path.suffix.lower()
    if suffix in {".py", ".pyi", ".pyw"}:
        return "\033[38;5;110m"
    return "\033[38;5;252m"


def highlight_substring(text: str, query: str) -> str:
    """Highlight first case-insensitive substring match in ``text``."""
    if not query:
        return text
    folded_text = text.casefold()
    folded_query = query.casefold()
    idx = folded_text.find(folded_query)
    if idx < 0:
        return text
    end = idx + len(query)
    return text[:idx] + "\033[7;1m" + text[idx:end] + "\033[27;22m" + text[end:]


def format_tree_entry(
    entry: TreeEntry,
    root: Path,
    expanded: set[Path],
    git_status_overlay: dict[Path, int] | None = None,
    search_query: str = "",
    show_size_labels: bool = True,
) -> str:
    """Render one tree/search-hit row as ANSI-styled display text."""
    if entry.kind == "search_hit":
        indent = "  " * max(0, entry.depth - 1)
        marker_color = "\033[38;5;44m"
        text_color = "\033[38;5;250m"
        reset = "\033[0m"
        content = highlight_substring((entry.display or "").lstrip(), search_query)
        return f"{indent}{marker_color}· {reset}{text_color}{content}{reset}"

    indent = "  " * entry.depth
    if entry.path == root:
        name = f"{root.name or str(root)}/"
    else:
        name = entry.path.name + ("/" if entry.is_dir else "")
    dir_color = "\033[1;34m"
    file_color = file_color_for(entry.path)
    size_color = "\033[38;5;109m"
    marker_color = "\033[38;5;44m"
    reset = "\033[0m"
    badges = format_git_status_badges(entry.path, git_status_overlay)
    if entry.is_dir:
        marker = "▾ " if entry.path.resolve() in expanded else "▸ "
        return f"{indent}{marker_color}{marker}{reset}{dir_color}{name}{reset}{badges}"

    # Align file names under the parent directory arrow column.
    indent = "  " * max(0, entry.depth - 1)
    marker = "  "
    size_label = ""
    if show_size_labels and entry.file_size is not None and entry.file_size >= TREE_SIZE_LABEL_MIN_BYTES:
        size_kb = entry.file_size // 1024
        size_label = f"{size_color} [{size_kb} KB]{reset}"
    return f"{indent}{marker}{file_color}{name}{reset}{size_label}{badges}"
