"""Filtered tree projections for file and content-search modes."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from ..search.content import ContentMatch
from .build import safe_file_size
from .types import TreeEntry


def filter_tree_entries_for_files(
    root: Path,
    expanded: set[Path],
    show_hidden: bool,
    matched_files: Iterable[Path],
    skip_gitignored: bool = False,
    workspace_root: Path | None = None,
    workspace_section: int | None = None,
) -> tuple[list[TreeEntry], set[Path]]:
    """Build filtered tree for matched files and their ancestor directories."""
    root = root.resolve()
    section_root = (workspace_root or root).resolve()
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
        """Sort directories before files and then by lowercase name."""
        is_dir = path in visible_dirs
        return (not is_dir, path.name.lower())

    for parent, children in children_by_parent.items():
        children.sort(key=child_sort_key)

    filtered_entries: list[TreeEntry] = [
        TreeEntry(
            root,
            0,
            True,
            workspace_root=section_root,
            workspace_section=workspace_section,
        )
    ]

    def walk(directory: Path, depth: int) -> None:
        """Emit filtered directory/file rows recursively."""
        for child in children_by_parent.get(directory, []):
            is_dir = child in visible_dirs
            filtered_entries.append(
                TreeEntry(
                    child,
                    depth,
                    is_dir,
                    file_size=safe_file_size(child, is_dir),
                    workspace_root=section_root,
                    workspace_section=workspace_section,
                )
            )
            if is_dir and child in render_expanded:
                walk(child, depth + 1)

    if root in render_expanded:
        walk(root, 1)

    if not filtered_entries:
        filtered_entries = [
            TreeEntry(
                root,
                0,
                True,
                workspace_root=section_root,
                workspace_section=workspace_section,
            )
        ]
    return filtered_entries, render_expanded


def filter_tree_entries_for_content_matches(
    root: Path,
    expanded: set[Path],
    matches_by_file: dict[Path, list[ContentMatch]],
    collapsed_dirs: set[Path] | None = None,
    workspace_root: Path | None = None,
    workspace_section: int | None = None,
) -> tuple[list[TreeEntry], set[Path]]:
    """Build content-search tree including synthetic hit rows under files."""
    root = root.resolve()
    section_root = (workspace_root or root).resolve()
    visible_dirs: set[Path] = {root}
    visible_files: set[Path] = set()
    normalized_matches: dict[Path, list[ContentMatch]] = {}
    forced_expanded: set[Path] = {root}
    collapsed = {
        path.resolve()
        for path in (collapsed_dirs or set())
        if path.resolve().is_relative_to(root)
    }

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

    render_expanded = (set(expanded) | forced_expanded) - collapsed
    render_expanded.add(root)
    children_by_parent: dict[Path, list[Path]] = {}
    for path in visible_dirs | visible_files:
        if path == root:
            continue
        parent = path.parent
        children_by_parent.setdefault(parent, []).append(path)

    def child_sort_key(path: Path) -> tuple[bool, str]:
        """Sort directories before files and then by lowercase name."""
        is_dir = path in visible_dirs
        return (not is_dir, path.name.lower())

    for parent, children in children_by_parent.items():
        children.sort(key=child_sort_key)

    filtered_entries: list[TreeEntry] = [
        TreeEntry(
            root,
            0,
            True,
            workspace_root=section_root,
            workspace_section=workspace_section,
        )
    ]

    def walk(directory: Path, depth: int) -> None:
        """Emit directory/file rows and content-hit children for visible files."""
        for child in children_by_parent.get(directory, []):
            is_dir = child in visible_dirs
            filtered_entries.append(
                TreeEntry(
                    child,
                    depth,
                    is_dir,
                    file_size=safe_file_size(child, is_dir),
                    workspace_root=section_root,
                    workspace_section=workspace_section,
                )
            )
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
                        workspace_root=section_root,
                        workspace_section=workspace_section,
                    )
                )

    if root in render_expanded:
        walk(root, 1)

    if not filtered_entries:
        filtered_entries = [
            TreeEntry(
                root,
                0,
                True,
                workspace_root=section_root,
                workspace_section=workspace_section,
            )
        ]
    return filtered_entries, render_expanded


def find_content_hit_index(
    entries: list[TreeEntry],
    preferred_path: Path,
    preferred_line: int | None = None,
    preferred_column: int | None = None,
    preferred_workspace_section: int | None = None,
) -> int | None:
    """Find best hit index for a file, preferring exact line/column when given."""
    preferred_resolved = preferred_path.resolve()
    first_hit_in_file: int | None = None
    first_hit_in_section: int | None = None
    exact_hit_in_section: int | None = None
    for idx, entry in enumerate(entries):
        if entry.kind != "search_hit":
            continue
        if entry.path.resolve() != preferred_resolved:
            continue
        if first_hit_in_file is None:
            first_hit_in_file = idx
        if (
            preferred_workspace_section is not None
            and entry.workspace_section == preferred_workspace_section
            and first_hit_in_section is None
        ):
            first_hit_in_section = idx
        if preferred_line is not None and entry.line != preferred_line:
            continue
        if preferred_column is not None and entry.column != preferred_column:
            continue
        if preferred_line is not None or preferred_column is not None:
            if (
                preferred_workspace_section is not None
                and entry.workspace_section == preferred_workspace_section
            ):
                exact_hit_in_section = idx
                continue
            return idx
    if exact_hit_in_section is not None:
        return exact_hit_in_section
    if first_hit_in_section is not None:
        return first_hit_in_section
    return first_hit_in_file
