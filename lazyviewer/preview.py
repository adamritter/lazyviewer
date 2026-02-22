from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import os
import sys
from pathlib import Path

from .git_status import build_unified_diff_preview_for_path
from .gitignore import get_gitignore_matcher
from .highlight import colorize_source, read_text, sanitize_terminal_text

DIR_PREVIEW_DEFAULT_DEPTH = 3
DIR_PREVIEW_INITIAL_MAX_ENTRIES = 400
DIR_PREVIEW_GROWTH_STEP = 400
DIR_PREVIEW_HARD_MAX_ENTRIES = 20_000
DIR_PREVIEW_CACHE_MAX = 128
BINARY_PROBE_BYTES = 4_096
COLORIZE_MAX_FILE_BYTES = 256_000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_DIR_PREVIEW_CACHE: OrderedDict[tuple[str, bool, int, int, bool, int], tuple[str, bool]] = OrderedDict()


@dataclass(frozen=True)
class RenderedPath:
    text: str
    is_directory: bool
    truncated: bool
    image_path: Path | None = None
    image_format: str | None = None
    is_git_diff_preview: bool = False


def _cache_key_for_directory(
    root_dir: Path,
    show_hidden: bool,
    max_depth: int,
    max_entries: int,
    skip_gitignored: bool,
) -> tuple[str, bool, int, int, bool, int] | None:
    try:
        resolved = root_dir.resolve()
        mtime_ns = resolved.stat().st_mtime_ns
    except Exception:
        return None
    return (str(resolved), show_hidden, max_depth, max_entries, skip_gitignored, int(mtime_ns))


def _cache_get(key: tuple[str, bool, int, int, bool, int] | None) -> tuple[str, bool] | None:
    if key is None:
        return None
    cached = _DIR_PREVIEW_CACHE.get(key)
    if cached is None:
        return None
    _DIR_PREVIEW_CACHE.move_to_end(key)
    return cached


def _cache_put(key: tuple[str, bool, int, int, bool, int] | None, preview: str, truncated: bool) -> None:
    if key is None:
        return
    _DIR_PREVIEW_CACHE[key] = (preview, truncated)
    _DIR_PREVIEW_CACHE.move_to_end(key)
    while len(_DIR_PREVIEW_CACHE) > DIR_PREVIEW_CACHE_MAX:
        _DIR_PREVIEW_CACHE.popitem(last=False)


def clear_directory_preview_cache() -> None:
    _DIR_PREVIEW_CACHE.clear()


def build_directory_preview(
    root_dir: Path,
    show_hidden: bool,
    max_depth: int = DIR_PREVIEW_DEFAULT_DEPTH,
    max_entries: int = DIR_PREVIEW_INITIAL_MAX_ENTRIES,
    skip_gitignored: bool = False,
) -> tuple[str, bool]:
    cache_key = _cache_key_for_directory(root_dir, show_hidden, max_depth, max_entries, skip_gitignored)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    ignore_matcher = get_gitignore_matcher(root_dir) if skip_gitignored else None

    dir_color = "\033[1;34m"
    file_color = "\033[38;5;252m"
    branch_color = "\033[2;38;5;245m"
    note_color = "\033[2;38;5;250m"
    reset = "\033[0m"

    try:
        root_label = f"{root_dir.resolve()}/"
    except Exception:
        root_label = f"{root_dir}/"
    lines_out: list[str] = [f"{dir_color}{root_label}{reset}", ""]
    emitted = 0

    def iter_children(directory: Path) -> tuple[list[tuple[str, Path, bool]], Exception | None]:
        children: list[tuple[str, Path, bool]] = []
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
                    children.append((name, child_path, is_dir))
        except (PermissionError, OSError) as exc:
            return [], exc

        children.sort(key=lambda item: (not item[2], item[0].lower()))
        return children, None

    def walk(directory: Path, prefix: str, depth: int) -> None:
        nonlocal emitted
        if depth > max_depth or emitted >= max_entries:
            return

        children, scan_error = iter_children(directory)
        if scan_error is not None:
            exc = scan_error
            lines_out.append(f"{branch_color}{prefix}└─{reset} {note_color}<error: {exc}>{reset}")
            return

        for idx, (name, child_path, is_dir) in enumerate(children):
            if emitted >= max_entries:
                break
            last = idx == len(children) - 1
            branch = "└─ " if last else "├─ "
            suffix = "/" if is_dir else ""
            name_color = dir_color if is_dir else file_color
            lines_out.append(f"{branch_color}{prefix}{branch}{reset}{name_color}{name}{suffix}{reset}")
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


def build_rendered_for_path(
    target: Path,
    show_hidden: bool,
    style: str,
    no_color: bool,
    dir_max_depth: int = DIR_PREVIEW_DEFAULT_DEPTH,
    dir_max_entries: int = DIR_PREVIEW_INITIAL_MAX_ENTRIES,
    dir_skip_gitignored: bool = False,
    prefer_git_diff: bool = True,
) -> RenderedPath:
    if target.is_dir():
        preview, truncated = build_directory_preview(
            target,
            show_hidden,
            max_depth=dir_max_depth,
            max_entries=dir_max_entries,
            skip_gitignored=dir_skip_gitignored,
        )
        return RenderedPath(text=preview, is_directory=True, truncated=truncated)

    try:
        file_size = target.stat().st_size
    except Exception:
        file_size = -1

    try:
        with target.open("rb") as handle:
            sample = handle.read(BINARY_PROBE_BYTES)
    except Exception:
        sample = b""
    if sample.startswith(PNG_SIGNATURE):
        try:
            image_path = target.resolve()
        except Exception:
            image_path = target
        return RenderedPath(
            text=f"{target}\n\n<PNG image preview via Kitty graphics protocol>",
            is_directory=False,
            truncated=False,
            image_path=image_path,
            image_format="png",
        )
    if b"\x00" in sample:
        if file_size >= 0:
            message = f"{target}\n\n<binary file: {file_size} bytes>"
        else:
            message = f"{target}\n\n<binary file>"
        return RenderedPath(text=message, is_directory=False, truncated=False)

    if prefer_git_diff:
        diff_preview = build_unified_diff_preview_for_path(
            target,
            colorize=not no_color and os.isatty(sys.stdout.fileno()),
            style=style,
        )
        if diff_preview:
            return RenderedPath(
                text=diff_preview,
                is_directory=False,
                truncated=False,
                is_git_diff_preview=True,
            )

    try:
        source = read_text(target)
    except Exception as exc:
        return RenderedPath(
            text=f"{target}\n\n<error reading file: {exc}>",
            is_directory=False,
            truncated=False,
        )
    source = sanitize_terminal_text(source)
    skip_colorize_for_size = file_size > COLORIZE_MAX_FILE_BYTES if file_size >= 0 else False
    if no_color or skip_colorize_for_size:
        return RenderedPath(text=source, is_directory=False, truncated=False)
    if os.isatty(sys.stdout.fileno()):
        return RenderedPath(
            text=colorize_source(source, target, style),
            is_directory=False,
            truncated=False,
        )
    return RenderedPath(text=source, is_directory=False, truncated=False)
