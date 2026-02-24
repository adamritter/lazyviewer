"""Tree-entry construction from filesystem state."""

from __future__ import annotations

from pathlib import Path

from ..gitignore import get_gitignore_matcher
from .types import TreeEntry


def safe_file_size(path: Path, is_dir: bool) -> int | None:
    """Return file size for files, otherwise ``None`` or on stat failure."""
    if is_dir:
        return None
    try:
        return int(path.stat().st_size)
    except OSError:
        return None


def build_tree_entries(
    root: Path,
    expanded: set[Path],
    show_hidden: bool,
    skip_gitignored: bool = False,
) -> list[TreeEntry]:
    """Build full tree-entry list rooted at ``root`` honoring expansion state."""
    root = root.resolve()
    entries: list[TreeEntry] = [TreeEntry(root, 0, True)]
    ignore_matcher = get_gitignore_matcher(root) if skip_gitignored else None

    def walk(directory: Path, depth: int) -> None:
        """Depth-first traversal adding visible children for expanded directories."""
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
            entries.append(TreeEntry(child, depth, is_dir, file_size=safe_file_size(child, is_dir)))
            if is_dir and child.resolve() in expanded:
                walk(child, depth + 1)

    if root in expanded:
        walk(root, 1)
    return entries
