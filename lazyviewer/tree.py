from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TreeEntry:
    path: Path
    depth: int
    is_dir: bool


def file_color_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".py", ".pyi", ".pyw"}:
        return "\033[38;5;110m"
    return "\033[38;5;252m"


def compute_left_width(total_width: int) -> int:
    if total_width <= 60:
        return max(16, total_width // 2)
    return max(20, min(40, total_width // 3))


def clamp_left_width(total_width: int, desired_left: int) -> int:
    max_possible = max(1, total_width - 2)
    min_left = max(12, min(20, total_width - 12))
    max_left = max(min_left, total_width - 12)
    max_left = min(max_left, max_possible)
    min_left = min(min_left, max_left)
    return max(min_left, min(desired_left, max_left))


def build_tree_entries(root: Path, expanded: set[Path], show_hidden: bool) -> list[TreeEntry]:
    root = root.resolve()
    entries: list[TreeEntry] = [TreeEntry(root, 0, True)]

    def walk(directory: Path, depth: int) -> None:
        try:
            children = list(directory.iterdir())
        except (PermissionError, OSError):
            return
        if not show_hidden:
            children = [p for p in children if not p.name.startswith(".")]
        children = sorted(children, key=lambda p: (not p.is_dir(), p.name.lower()))
        for child in children:
            is_dir = child.is_dir()
            entries.append(TreeEntry(child, depth, is_dir))
            if is_dir and child.resolve() in expanded:
                walk(child, depth + 1)

    if root in expanded:
        walk(root, 1)
    return entries


def filter_tree_entries_for_files(
    root: Path,
    expanded: set[Path],
    show_hidden: bool,
    matched_files: Iterable[Path],
) -> tuple[list[TreeEntry], set[Path]]:
    root = root.resolve()
    visible_paths: set[Path] = {root}
    forced_expanded: set[Path] = {root}

    for raw_path in matched_files:
        file_path = raw_path.resolve()
        try:
            file_path.relative_to(root)
        except ValueError:
            continue

        visible_paths.add(file_path)
        parent = file_path.parent.resolve()
        while True:
            try:
                parent.relative_to(root)
            except ValueError:
                break
            visible_paths.add(parent)
            forced_expanded.add(parent)
            if parent == root or parent.parent == parent:
                break
            parent = parent.parent.resolve()

    render_expanded = set(expanded) | forced_expanded
    source_entries = build_tree_entries(root, render_expanded, show_hidden)
    filtered_entries = [entry for entry in source_entries if entry.path.resolve() in visible_paths]
    if not filtered_entries:
        filtered_entries = [TreeEntry(root, 0, True)]
    return filtered_entries, render_expanded


def format_tree_entry(entry: TreeEntry, root: Path, expanded: set[Path]) -> str:
    indent = "  " * entry.depth
    if entry.path == root:
        name = f"{root.name or str(root)}/"
    else:
        name = entry.path.name + ("/" if entry.is_dir else "")
    dir_color = "\033[1;34m"
    file_color = file_color_for(entry.path)
    marker_color = "\033[38;5;44m"
    reset = "\033[0m"
    if entry.is_dir:
        marker = "▾ " if entry.path.resolve() in expanded else "▸ "
        return f"{indent}{marker_color}{marker}{reset}{dir_color}{name}{reset}"

    # Align file names under the parent directory arrow column.
    indent = "  " * max(0, entry.depth - 1)
    marker = "  "
    return f"{indent}{marker}{file_color}{name}{reset}"
