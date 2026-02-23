"""Render directory previews with bounded depth, size labels, and cache reuse.

Preview output is ANSI-formatted tree text used by the source pane when the
selected path is a directory. Results are memoized with keys that include root
mtime and git-overlay signature.
"""

from __future__ import annotations

from collections import OrderedDict
import os
from pathlib import Path

from ..git_status import format_git_status_badges
from ..gitignore import get_gitignore_matcher

DIR_PREVIEW_DEFAULT_DEPTH = 3
DIR_PREVIEW_INITIAL_MAX_ENTRIES = 400
DIR_PREVIEW_GROWTH_STEP = 400
DIR_PREVIEW_HARD_MAX_ENTRIES = 20_000
DIR_PREVIEW_CACHE_MAX = 128
TREE_SIZE_LABEL_MIN_BYTES = 10 * 1024

_DIR_PREVIEW_CACHE: OrderedDict[tuple[str, bool, int, int, bool, bool, int, int], tuple[str, bool]] = OrderedDict()


def _cache_key_for_directory(
    root_dir: Path,
    show_hidden: bool,
    max_depth: int,
    max_entries: int,
    skip_gitignored: bool,
    show_size_labels: bool,
    git_overlay_signature: int,
) -> tuple[str, bool, int, int, bool, bool, int, int] | None:
    """Build cache key for a directory preview request."""
    try:
        resolved = root_dir.resolve()
        mtime_ns = resolved.stat().st_mtime_ns
    except Exception:
        return None
    return (
        str(resolved),
        show_hidden,
        max_depth,
        max_entries,
        skip_gitignored,
        show_size_labels,
        int(mtime_ns),
        git_overlay_signature,
    )


def _cache_get(key: tuple[str, bool, int, int, bool, bool, int, int] | None) -> tuple[str, bool] | None:
    """Return cached preview/truncation pair and refresh LRU order."""
    if key is None:
        return None
    cached = _DIR_PREVIEW_CACHE.get(key)
    if cached is None:
        return None
    _DIR_PREVIEW_CACHE.move_to_end(key)
    return cached


def _cache_put(key: tuple[str, bool, int, int, bool, bool, int, int] | None, preview: str, truncated: bool) -> None:
    """Insert preview result into LRU cache with bounded size."""
    if key is None:
        return
    _DIR_PREVIEW_CACHE[key] = (preview, truncated)
    _DIR_PREVIEW_CACHE.move_to_end(key)
    while len(_DIR_PREVIEW_CACHE) > DIR_PREVIEW_CACHE_MAX:
        _DIR_PREVIEW_CACHE.popitem(last=False)


def clear_directory_preview_cache() -> None:
    """Clear in-memory directory preview cache."""
    _DIR_PREVIEW_CACHE.clear()


def _directory_overlay_signature(root_dir: Path, git_status_overlay: dict[Path, int] | None) -> int:
    """Compute stable hash of overlay entries that affect directory preview rows."""
    if not git_status_overlay:
        return 0

    try:
        root = root_dir.resolve()
    except Exception:
        root = root_dir

    entries: list[tuple[str, int]] = []
    for raw_path, flags in git_status_overlay.items():
        if flags == 0:
            continue
        try:
            path = raw_path.resolve()
        except Exception:
            path = raw_path

        if path == root:
            entries.append((".", int(flags)))
            continue
        if not path.is_relative_to(root):
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except Exception:
            continue
        entries.append((rel, int(flags)))

    if not entries:
        return 0
    entries.sort()
    return hash(tuple(entries))


def build_directory_preview(
    root_dir: Path,
    show_hidden: bool,
    max_depth: int = DIR_PREVIEW_DEFAULT_DEPTH,
    max_entries: int = DIR_PREVIEW_INITIAL_MAX_ENTRIES,
    skip_gitignored: bool = False,
    git_status_overlay: dict[Path, int] | None = None,
    show_size_labels: bool = True,
) -> tuple[str, bool]:
    """Render directory tree preview and return ``(text, truncated)``."""
    overlay_signature = _directory_overlay_signature(root_dir, git_status_overlay)
    cache_key = _cache_key_for_directory(
        root_dir,
        show_hidden,
        max_depth,
        max_entries,
        skip_gitignored,
        show_size_labels,
        overlay_signature,
    )
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    ignore_matcher = get_gitignore_matcher(root_dir) if skip_gitignored else None

    dir_color = "\033[1;34m"
    file_color = "\033[38;5;252m"
    branch_color = "\033[2;38;5;245m"
    note_color = "\033[2;38;5;250m"
    size_color = "\033[38;5;109m"
    reset = "\033[0m"

    try:
        root_label = f"{root_dir.resolve()}/"
    except Exception:
        root_label = f"{root_dir}/"
    root_badges = format_git_status_badges(root_dir, git_status_overlay)
    lines_out: list[str] = [f"{dir_color}{root_label}{reset}{root_badges}", ""]
    emitted = 0

    def iter_children(directory: Path) -> tuple[list[tuple[str, Path, bool, int | None]], Exception | None]:
        """Scan and sort one directory's visible children for preview rendering."""
        children: list[tuple[str, Path, bool, int | None]] = []
        try:
            with os.scandir(directory) as entries:
                for child in entries:
                    name = child.name
                    if not show_hidden and name.startswith("."):
                        continue
                    child_path = Path(child.path)
                    if ignore_matcher is not None and ignore_matcher.is_ignored(child_path):
                        continue
                    try:
                        is_dir = child.is_dir(follow_symlinks=False)
                    except OSError:
                        is_dir = False
                    size_bytes: int | None = None
                    if not is_dir:
                        try:
                            size_bytes = int(child.stat(follow_symlinks=False).st_size)
                        except OSError:
                            size_bytes = None
                    children.append((name, child_path, is_dir, size_bytes))
        except (PermissionError, OSError) as exc:
            return [], exc

        children.sort(key=lambda item: (not item[2], item[0].lower()))
        return children, None

    def walk(directory: Path, prefix: str, depth: int) -> None:
        """Emit preview rows depth-first until depth/entry limits are reached."""
        nonlocal emitted
        if depth > max_depth or emitted >= max_entries:
            return

        children, scan_error = iter_children(directory)
        if scan_error is not None:
            exc = scan_error
            lines_out.append(f"{branch_color}{prefix}└─{reset} {note_color}<error: {exc}>{reset}")
            return

        for idx, (name, child_path, is_dir, size_bytes) in enumerate(children):
            if emitted >= max_entries:
                break
            last = idx == len(children) - 1
            branch = "└─ " if last else "├─ "
            suffix = "/" if is_dir else ""
            name_color = dir_color if is_dir else file_color
            size_label = ""
            if show_size_labels and (not is_dir) and size_bytes is not None and size_bytes >= TREE_SIZE_LABEL_MIN_BYTES:
                size_kb = size_bytes // 1024
                size_label = f"{size_color} [{size_kb} KB]{reset}"
            badges = format_git_status_badges(child_path, git_status_overlay)
            lines_out.append(f"{branch_color}{prefix}{branch}{reset}{name_color}{name}{suffix}{reset}{size_label}{badges}")
            emitted += 1
            if is_dir:
                walk(child_path, prefix + ("   " if last else "│  "), depth + 1)

    walk(root_dir, "", 1)
    truncated = emitted >= max_entries
    if truncated:
        lines_out.append("")
        lines_out.append(f"{note_color}... truncated after {max_entries} entries ...{reset}")

    preview = "\n".join(lines_out)
    _cache_put(cache_key, preview, truncated)
    return preview, truncated
