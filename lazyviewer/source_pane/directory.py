"""Render directory previews with bounded depth, size labels, and cache reuse.

Preview output is ANSI-formatted tree text used by the source pane when the
selected path is a directory. Results are memoized with keys that include root
mtime and git-overlay signature, then validated against scanned directory mtimes
to avoid stale nested previews.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import os
import re
from pathlib import Path

from .syntax import sanitize_terminal_text
from ..git_status import format_git_status_badges
from ..gitignore import get_gitignore_matcher

DIR_PREVIEW_DEFAULT_DEPTH = 32
DIR_PREVIEW_INITIAL_MAX_ENTRIES = 1_000
DIR_PREVIEW_GROWTH_STEP = 500
DIR_PREVIEW_HARD_MAX_ENTRIES = 20_000
DIR_PREVIEW_CACHE_MAX = 128
TREE_SIZE_LABEL_MIN_BYTES = 10 * 1024
DOC_SUMMARY_READ_BYTES = 4_096
DOC_SUMMARY_MAX_FILE_BYTES = 256 * 1024
DOC_SUMMARY_MAX_CHARS = 96

_CODING_COOKIE_RE = re.compile(r"^#.*coding[:=]\s*[-\w.]+")
_TRIPLE_QUOTE_PREFIXES = ('"""', "'''")
_LINE_COMMENT_PREFIXES = ("#", "//", "--", ";")


@dataclass(frozen=True)
class _DirectoryPreviewCacheEntry:
    """Cached directory preview payload plus watched-directory mtimes."""

    preview: str
    truncated: bool
    watched_directory_mtimes: tuple[tuple[str, int], ...]


_DIR_PREVIEW_CACHE: OrderedDict[tuple[str, bool, int, int, bool, bool, int, int], _DirectoryPreviewCacheEntry] = (
    OrderedDict()
)


def _normalize_doc_summary(text: str) -> str | None:
    """Normalize doc text to one safe short line."""
    candidate = sanitize_terminal_text(" ".join(text.strip().split()))
    if not candidate:
        return None
    if len(candidate) > DOC_SUMMARY_MAX_CHARS:
        return candidate[: DOC_SUMMARY_MAX_CHARS - 3].rstrip() + "..."
    return candidate


def _extract_triple_quote_summary(lines: list[str], start_idx: int, delimiter: str) -> str | None:
    """Extract one-line summary from a top-level triple-quoted block."""
    first = lines[start_idx].lstrip()
    body = first[len(delimiter) :]
    if delimiter in body:
        return _normalize_doc_summary(body.split(delimiter, 1)[0])

    summary = _normalize_doc_summary(body)
    if summary:
        return summary

    for idx in range(start_idx + 1, len(lines)):
        line = lines[idx]
        if delimiter in line:
            return _normalize_doc_summary(line.split(delimiter, 1)[0])
        summary = _normalize_doc_summary(line)
        if summary:
            return summary
    return None


def _extract_block_comment_summary(lines: list[str], start_idx: int) -> str | None:
    """Extract one-line summary from C/JS-style block comment at file start."""
    first = lines[start_idx].lstrip()
    body = first[2:]
    if "*/" in body:
        return _normalize_doc_summary(body.split("*/", 1)[0].lstrip("*").strip())

    summary = _normalize_doc_summary(body.lstrip("*").strip())
    if summary:
        return summary

    for idx in range(start_idx + 1, len(lines)):
        line = lines[idx]
        if "*/" in line:
            return _normalize_doc_summary(line.split("*/", 1)[0].lstrip("*").strip())
        summary = _normalize_doc_summary(line.lstrip("*").strip())
        if summary:
            return summary
    return None


def _extract_line_comment_summary(lines: list[str], start_idx: int, prefix: str) -> str | None:
    """Extract one-line summary from contiguous top-of-file line comments."""
    for idx in range(start_idx, len(lines)):
        stripped = lines[idx].lstrip()
        if not stripped:
            continue
        if not stripped.startswith(prefix):
            break
        summary = _normalize_doc_summary(stripped[len(prefix) :])
        if summary:
            return summary
    return None


def _top_file_doc_summary(path: Path, size_bytes: int | None) -> str | None:
    """Return top-of-file one-line documentation summary when present."""
    if size_bytes is not None and size_bytes > DOC_SUMMARY_MAX_FILE_BYTES:
        return None

    try:
        with path.open("rb") as handle:
            sample = handle.read(DOC_SUMMARY_READ_BYTES)
    except OSError:
        return None
    if not sample or b"\x00" in sample:
        return None

    text = sample.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if not lines:
        return None
    if lines[0].startswith("\ufeff"):
        lines[0] = lines[0].lstrip("\ufeff")

    idx = 0
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx >= len(lines):
        return None

    first_stripped = lines[idx].lstrip()
    if first_stripped.startswith("#!"):
        idx += 1
    if idx < len(lines) and _CODING_COOKIE_RE.match(lines[idx].strip()):
        idx += 1
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx >= len(lines):
        return None

    first = lines[idx].lstrip()
    for delimiter in _TRIPLE_QUOTE_PREFIXES:
        if first.startswith(delimiter):
            return _extract_triple_quote_summary(lines, idx, delimiter)
    if first.startswith("/*"):
        return _extract_block_comment_summary(lines, idx)
    for prefix in _LINE_COMMENT_PREFIXES:
        if first.startswith(prefix):
            return _extract_line_comment_summary(lines, idx, prefix)
    return None


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
    _DIR_PREVIEW_CACHE.move_to_end(key)
    return cached.preview, cached.truncated


def _cache_put(
    key: tuple[str, bool, int, int, bool, bool, int, int] | None,
    preview: str,
    truncated: bool,
    watched_directory_mtimes: tuple[tuple[str, int], ...],
) -> None:
    """Insert preview result into LRU cache with bounded size."""
    if key is None:
        return
    _DIR_PREVIEW_CACHE[key] = _DirectoryPreviewCacheEntry(
        preview=preview,
        truncated=truncated,
        watched_directory_mtimes=watched_directory_mtimes,
    )
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

    def iter_children(directory: Path) -> tuple[list[tuple[str, Path, bool, int | None]], Exception | None]:
        """Scan and sort one directory's visible children for preview rendering."""
        try:
            resolved_directory = directory.resolve()
        except Exception:
            resolved_directory = directory
        try:
            watched_directory_mtimes[str(resolved_directory)] = int(resolved_directory.stat().st_mtime_ns)
        except Exception:
            pass

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
            doc_label = ""
            if show_size_labels and (not is_dir) and size_bytes is not None and size_bytes >= TREE_SIZE_LABEL_MIN_BYTES:
                size_kb = size_bytes // 1024
                size_label = f"{size_color} [{size_kb} KB]{reset}"
            if not is_dir:
                doc_summary = _top_file_doc_summary(child_path, size_bytes)
                if doc_summary:
                    doc_label = f"{doc_color}  -- {doc_summary}{reset}"
            badges = format_git_status_badges(child_path, git_status_overlay)
            lines_out.append(
                f"{branch_color}{prefix}{branch}{reset}{name_color}{name}{suffix}{reset}{size_label}{badges}{doc_label}"
            )
            emitted += 1
            if is_dir:
                walk(child_path, prefix + ("   " if last else "│  "), depth + 1)

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
    )
    return preview, truncated
