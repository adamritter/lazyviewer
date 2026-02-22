from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .git_status import GIT_STATUS_CHANGED, GIT_STATUS_UNTRACKED
from .gitignore import get_gitignore_matcher
from .search import ContentMatch


@dataclass(frozen=True)
class TreeEntry:
    path: Path
    depth: int
    is_dir: bool
    kind: str = "path"
    display: str | None = None
    line: int | None = None
    column: int | None = None


def file_color_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".py", ".pyi", ".pyw"}:
        return "\033[38;5;110m"
    return "\033[38;5;252m"


def _highlight_substring(text: str, query: str) -> str:
    if not query:
        return text
    folded_text = text.casefold()
    folded_query = query.casefold()
    idx = folded_text.find(folded_query)
    if idx < 0:
        return text
    end = idx + len(query)
    return (
        text[:idx]
        + "\033[1;30;43m"
        + text[idx:end]
        + "\033[0m\033[38;5;250m"
        + text[end:]
    )


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


def build_tree_entries(
    root: Path,
    expanded: set[Path],
    show_hidden: bool,
    skip_gitignored: bool = False,
) -> list[TreeEntry]:
    root = root.resolve()
    entries: list[TreeEntry] = [TreeEntry(root, 0, True)]
    ignore_matcher = get_gitignore_matcher(root) if skip_gitignored else None

    def walk(directory: Path, depth: int) -> None:
        try:
            children = list(directory.iterdir())
        except (PermissionError, OSError):
            return
        if not show_hidden:
            children = [p for p in children if not p.name.startswith(".")]
        children = sorted(children, key=lambda p: (not p.is_dir(), p.name.lower()))
        for child in children:
            if ignore_matcher is not None and ignore_matcher.is_ignored(child):
                continue
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
    skip_gitignored: bool = False,
) -> tuple[list[TreeEntry], set[Path]]:
    root = root.resolve()
    visible_dirs: set[Path] = {root}
    visible_files: set[Path] = set()
    forced_expanded: set[Path] = {root}

    for raw_path in matched_files:
        # matched_files comes from the cached project index and is already absolute.
        file_path = raw_path if raw_path.is_absolute() else (root / raw_path)
        if not file_path.is_relative_to(root):
            file_path = file_path.resolve()
            if not file_path.is_relative_to(root):
                continue

        visible_files.add(file_path)
        parent = file_path.parent
        while True:
            if not parent.is_relative_to(root):
                break
            visible_dirs.add(parent)
            forced_expanded.add(parent)
            if parent == root or parent.parent == parent:
                break
            parent = parent.parent

    render_expanded = set(expanded) | forced_expanded
    children_by_parent: dict[Path, list[Path]] = {}
    for path in visible_dirs | visible_files:
        if path == root:
            continue
        parent = path.parent
        children_by_parent.setdefault(parent, []).append(path)

    def child_sort_key(path: Path) -> tuple[bool, str]:
        is_dir = path in visible_dirs
        return (not is_dir, path.name.lower())

    for parent, children in children_by_parent.items():
        children.sort(key=child_sort_key)

    filtered_entries: list[TreeEntry] = [TreeEntry(root, 0, True)]

    def walk(directory: Path, depth: int) -> None:
        for child in children_by_parent.get(directory, []):
            is_dir = child in visible_dirs
            filtered_entries.append(TreeEntry(child, depth, is_dir))
            if is_dir and child in render_expanded:
                walk(child, depth + 1)

    if root in render_expanded:
        walk(root, 1)

    if not filtered_entries:
        filtered_entries = [TreeEntry(root, 0, True)]
    return filtered_entries, render_expanded


def filter_tree_entries_for_content_matches(
    root: Path,
    expanded: set[Path],
    matches_by_file: dict[Path, list[ContentMatch]],
) -> tuple[list[TreeEntry], set[Path]]:
    root = root.resolve()
    visible_dirs: set[Path] = {root}
    visible_files: set[Path] = set()
    normalized_matches: dict[Path, list[ContentMatch]] = {}
    forced_expanded: set[Path] = {root}

    for raw_path, matches in matches_by_file.items():
        file_path = raw_path if raw_path.is_absolute() else (root / raw_path)
        if not file_path.is_relative_to(root):
            file_path = file_path.resolve()
            if not file_path.is_relative_to(root):
                continue
        if not matches:
            continue

        normalized_matches[file_path] = sorted(matches, key=lambda item: (item.line, item.column, item.preview))
        visible_files.add(file_path)
        parent = file_path.parent
        while True:
            if not parent.is_relative_to(root):
                break
            visible_dirs.add(parent)
            forced_expanded.add(parent)
            if parent == root or parent.parent == parent:
                break
            parent = parent.parent

    render_expanded = set(expanded) | forced_expanded
    children_by_parent: dict[Path, list[Path]] = {}
    for path in visible_dirs | visible_files:
        if path == root:
            continue
        parent = path.parent
        children_by_parent.setdefault(parent, []).append(path)

    def child_sort_key(path: Path) -> tuple[bool, str]:
        is_dir = path in visible_dirs
        return (not is_dir, path.name.lower())

    for parent, children in children_by_parent.items():
        children.sort(key=child_sort_key)

    filtered_entries: list[TreeEntry] = [TreeEntry(root, 0, True)]

    def walk(directory: Path, depth: int) -> None:
        for child in children_by_parent.get(directory, []):
            is_dir = child in visible_dirs
            filtered_entries.append(TreeEntry(child, depth, is_dir))
            if is_dir and child in render_expanded:
                walk(child, depth + 1)
                continue
            if is_dir:
                continue
            for hit in normalized_matches.get(child, []):
                filtered_entries.append(
                    TreeEntry(
                        path=child,
                        depth=depth + 1,
                        is_dir=False,
                        kind="search_hit",
                        display=hit.preview,
                        line=hit.line,
                        column=hit.column,
                    )
                )

    if root in render_expanded:
        walk(root, 1)

    if not filtered_entries:
        filtered_entries = [TreeEntry(root, 0, True)]
    return filtered_entries, render_expanded


def next_file_entry_index(
    entries: list[TreeEntry],
    selected_idx: int,
    direction: int,
) -> int | None:
    if not entries or direction == 0:
        return None
    step = 1 if direction > 0 else -1
    idx = selected_idx + step
    while 0 <= idx < len(entries):
        if not entries[idx].is_dir:
            return idx
        idx += step
    return None


def next_directory_entry_index(
    entries: list[TreeEntry],
    selected_idx: int,
    direction: int,
) -> int | None:
    if not entries or direction == 0:
        return None
    step = 1 if direction > 0 else -1
    idx = selected_idx + step
    while 0 <= idx < len(entries):
        if entries[idx].is_dir:
            return idx
        idx += step
    return None


def next_opened_directory_entry_index(
    entries: list[TreeEntry],
    selected_idx: int,
    direction: int,
    expanded: set[Path],
) -> int | None:
    if not entries or direction == 0:
        return None
    step = 1 if direction > 0 else -1
    idx = selected_idx + step
    while 0 <= idx < len(entries):
        entry = entries[idx]
        if entry.is_dir and entry.path.resolve() in expanded:
            return idx
        idx += step
    return None


def next_index_after_directory_subtree(entries: list[TreeEntry], directory_idx: int) -> int | None:
    if not entries or directory_idx < 0 or directory_idx >= len(entries):
        return None
    directory_entry = entries[directory_idx]
    if not directory_entry.is_dir:
        return None

    idx = directory_idx + 1
    while idx < len(entries) and entries[idx].depth > directory_entry.depth:
        idx += 1
    if idx >= len(entries):
        return None
    return idx


def _format_git_status_badges(path: Path, git_status_overlay: dict[Path, int] | None) -> str:
    if not git_status_overlay:
        return ""

    flags = git_status_overlay.get(path.resolve(), 0)
    if flags == 0:
        return ""

    badges: list[str] = []
    if flags & GIT_STATUS_CHANGED:
        badges.append("\033[38;5;214m[M]\033[0m")
    if flags & GIT_STATUS_UNTRACKED:
        badges.append("\033[38;5;42m[?]\033[0m")
    if not badges:
        return ""
    return " " + "".join(badges)


def format_tree_entry(
    entry: TreeEntry,
    root: Path,
    expanded: set[Path],
    git_status_overlay: dict[Path, int] | None = None,
    search_query: str = "",
) -> str:
    if entry.kind == "search_hit":
        indent = "  " * entry.depth
        marker_color = "\033[38;5;44m"
        text_color = "\033[38;5;250m"
        reset = "\033[0m"
        line_label = ""
        if entry.line is not None:
            if entry.column is not None:
                line_label = f"L{entry.line}:{entry.column} "
            else:
                line_label = f"L{entry.line} "
        content = _highlight_substring(entry.display or "", search_query)
        return f"{indent}{marker_color}· {reset}{text_color}{line_label}{content}{reset}"

    indent = "  " * entry.depth
    if entry.path == root:
        name = f"{root.name or str(root)}/"
    else:
        name = entry.path.name + ("/" if entry.is_dir else "")
    dir_color = "\033[1;34m"
    file_color = file_color_for(entry.path)
    marker_color = "\033[38;5;44m"
    reset = "\033[0m"
    badges = _format_git_status_badges(entry.path, git_status_overlay)
    if entry.is_dir:
        marker = "▾ " if entry.path.resolve() in expanded else "▸ "
        return f"{indent}{marker_color}{marker}{reset}{dir_color}{name}{reset}{badges}"

    # Align file names under the parent directory arrow column.
    indent = "  " * max(0, entry.depth - 1)
    marker = "  "
    return f"{indent}{marker}{file_color}{name}{reset}{badges}"
