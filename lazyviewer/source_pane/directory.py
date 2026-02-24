"""Render directory previews with bounded depth, size labels, and cache reuse.

Preview output is ANSI-formatted tree text used by the source pane when the
selected path is a directory. Results are memoized with keys that include root
mtime and git-overlay signature, then validated against scanned metadata.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from ..file_tree_model.doc_summary import clear_doc_summary_cache
from ..file_tree_model.fs import list_directory_children, maybe_gitignore_matcher
from ..git_status import format_git_status_badges
from ..tree_model.rendering import TREE_SIZE_LABEL_MIN_BYTES

DIR_PREVIEW_DEFAULT_DEPTH = 32
DIR_PREVIEW_INITIAL_MAX_ENTRIES = 1_000
DIR_PREVIEW_GROWTH_STEP = 500
DIR_PREVIEW_HARD_MAX_ENTRIES = 20_000
DIR_PREVIEW_CACHE_MAX = 128


@dataclass(frozen=True)
class _DirectoryPreviewCacheEntry:
    """Cached directory preview payload plus watched path metadata."""

    preview: str
    truncated: bool
    watched_directory_mtimes: tuple[tuple[str, int], ...]
    watched_file_signatures: tuple[tuple[str, int, int], ...]


_DIR_PREVIEW_CACHE: OrderedDict[tuple[str, bool, int, int, bool, bool, int, int], _DirectoryPreviewCacheEntry] = (
    OrderedDict()
)


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


def _watched_directory_mtimes_match(watched_directory_mtimes: tuple[tuple[str, int], ...]) -> bool:
    """Return whether directory mtimes captured for a cached preview are unchanged."""
    for path_text, cached_mtime_ns in watched_directory_mtimes:
        try:
            current_mtime_ns = Path(path_text).stat().st_mtime_ns
        except Exception:
            return False
        if int(current_mtime_ns) != int(cached_mtime_ns):
            return False
    return True


def _watched_file_signatures_match(watched_file_signatures: tuple[tuple[str, int, int], ...]) -> bool:
    """Return whether file mtime/size signatures captured for a cache entry are unchanged."""
    for path_text, cached_mtime_ns, cached_size in watched_file_signatures:
        try:
            stat = Path(path_text).stat()
        except Exception:
            return False
        if int(stat.st_mtime_ns) != int(cached_mtime_ns):
            return False
        if int(stat.st_size) != int(cached_size):
            return False
    return True


def _cache_get(key: tuple[str, bool, int, int, bool, bool, int, int] | None) -> tuple[str, bool] | None:
    """Return cached preview/truncation pair and refresh LRU order."""
    if key is None:
        return None
    cached = _DIR_PREVIEW_CACHE.get(key)
    if cached is None:
        return None
    if not _watched_directory_mtimes_match(cached.watched_directory_mtimes):
        _DIR_PREVIEW_CACHE.pop(key, None)
        return None
    if not _watched_file_signatures_match(cached.watched_file_signatures):
        _DIR_PREVIEW_CACHE.pop(key, None)
        return None
    _DIR_PREVIEW_CACHE.move_to_end(key)
    return cached.preview, cached.truncated


def _cache_put(
    key: tuple[str, bool, int, int, bool, bool, int, int] | None,
    preview: str,
    truncated: bool,
    watched_directory_mtimes: tuple[tuple[str, int], ...],
    watched_file_signatures: tuple[tuple[str, int, int], ...],
) -> None:
    """Insert preview result into LRU cache with bounded size."""
    if key is None:
        return
    _DIR_PREVIEW_CACHE[key] = _DirectoryPreviewCacheEntry(
        preview=preview,
        truncated=truncated,
        watched_directory_mtimes=watched_directory_mtimes,
        watched_file_signatures=watched_file_signatures,
    )
    _DIR_PREVIEW_CACHE.move_to_end(key)
    while len(_DIR_PREVIEW_CACHE) > DIR_PREVIEW_CACHE_MAX:
        _DIR_PREVIEW_CACHE.popitem(last=False)


def clear_directory_preview_cache() -> None:
    """Clear in-memory directory preview cache and doc-summary metadata cache."""
    _DIR_PREVIEW_CACHE.clear()
    clear_doc_summary_cache()


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

    ignore_matcher = maybe_gitignore_matcher(root_dir, skip_gitignored)

    dir_color = "\033[1;34m"
    file_color = "\033[38;5;252m"
    branch_color = "\033[2;38;5;245m"
    note_color = "\033[2;38;5;250m"
    doc_color = "\033[2;38;5;244m"
    size_color = "\033[38;5;109m"
    reset = "\033[0m"

    try:
        root_label = f"{root_dir.resolve()}/"
    except Exception:
        root_label = f"{root_dir}/"
    root_badges = format_git_status_badges(root_dir, git_status_overlay)
    lines_out: list[str] = [f"{dir_color}{root_label}{reset}{root_badges}", ""]
    emitted = 0
    watched_directory_mtimes: dict[str, int] = {}
    watched_file_signatures: dict[str, tuple[int, int]] = {}

    def walk(directory: Path, prefix: str, depth: int) -> None:
        """Emit preview rows depth-first until depth/entry limits are reached."""
        nonlocal emitted
        if depth > max_depth or emitted >= max_entries:
            return

        children, scan_error, directory_mtime_ns = list_directory_children(
            directory,
            show_hidden,
            ignore_matcher=ignore_matcher,
            git_status_overlay=git_status_overlay,
            include_doc_summaries=True,
        )
        if directory_mtime_ns is not None:
            try:
                resolved_directory = directory.resolve()
            except Exception:
                resolved_directory = directory
            watched_directory_mtimes[str(resolved_directory)] = int(directory_mtime_ns)
        if scan_error is not None:
            lines_out.append(f"{branch_color}{prefix}└─{reset} {note_color}<error: {scan_error}>{reset}")
            return

        for idx, child in enumerate(children):
            if emitted >= max_entries:
                break
            last = idx == len(children) - 1
            branch = "└─ " if last else "├─ "
            suffix = "/" if child.is_dir else ""
            name_color = dir_color if child.is_dir else file_color
            size_label = ""
            doc_label = ""
            if (
                show_size_labels
                and (not child.is_dir)
                and child.file_size is not None
                and child.file_size >= TREE_SIZE_LABEL_MIN_BYTES
            ):
                size_kb = child.file_size // 1024
                size_label = f"{size_color} [{size_kb} KB]{reset}"
            if not child.is_dir:
                if child.mtime_ns is not None and child.file_size is not None:
                    try:
                        resolved_file = child.path.resolve()
                    except Exception:
                        resolved_file = child.path
                    watched_file_signatures[str(resolved_file)] = (child.mtime_ns, child.file_size)
                if child.doc_summary:
                    doc_label = f"{doc_color}  -- {child.doc_summary}{reset}"

            badges = format_git_status_badges(child.path, git_status_overlay)
            lines_out.append(
                f"{branch_color}{prefix}{branch}{reset}{name_color}{child.name}{suffix}{reset}{size_label}{badges}{doc_label}"
            )
            emitted += 1
            if child.is_dir:
                walk(child.path, prefix + ("   " if last else "│  "), depth + 1)

    walk(root_dir, "", 1)
    truncated = emitted >= max_entries
    if truncated:
        lines_out.append("")
        lines_out.append(f"{note_color}... truncated after {max_entries} entries ...{reset}")

    preview = "\n".join(lines_out)
    _cache_put(
        cache_key,
        preview,
        truncated,
        watched_directory_mtimes=tuple(watched_directory_mtimes.items()),
        watched_file_signatures=tuple(
            (path_text, signature[0], signature[1])
            for path_text, signature in watched_file_signatures.items()
        ),
    )
    return preview, truncated
