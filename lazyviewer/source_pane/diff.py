"""Build annotated git-diff previews aligned to current source content.

The generated preview keeps full-file context, marks added/removed lines, and
optionally preserves syntax coloring while applying readable diff backgrounds.
Results are memoized with a git-signature-aware cache key.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess

from .syntax import colorize_source, read_text, sanitize_terminal_text
from ..file_tree_model.watch import build_git_watch_signature

GIT_DIFF_PREVIEW_CACHE_MAX = 128

_DIFF_PREVIEW_CACHE: OrderedDict[tuple[str, int, int, str, bool, str], str | None] = OrderedDict()
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")
_ADDED_BG_SGR = "48;2;36;74;52"
_REMOVED_BG_SGR = "48;2;92;43;49"
_DIFF_CONTRAST_8BIT = "246"
_DIFF_CONTRAST_TRUECOLOR = ("170", "170", "170")


@dataclass
class DiffHunk:
    """Parsed hunk metadata plus removed lines from unified diff text."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    removed_lines: list[str]


def _cache_get(key: tuple[str, int, int, str, bool, str]) -> tuple[bool, str | None]:
    """Lookup diff preview cache entry and refresh LRU order."""
    if key not in _DIFF_PREVIEW_CACHE:
        return False, None
    cached = _DIFF_PREVIEW_CACHE[key]
    _DIFF_PREVIEW_CACHE.move_to_end(key)
    return True, cached


def _cache_put(key: tuple[str, int, int, str, bool, str], value: str | None) -> None:
    """Insert diff preview cache entry and evict oldest overflow entries."""
    _DIFF_PREVIEW_CACHE[key] = value
    _DIFF_PREVIEW_CACHE.move_to_end(key)
    while len(_DIFF_PREVIEW_CACHE) > GIT_DIFF_PREVIEW_CACHE_MAX:
        _DIFF_PREVIEW_CACHE.popitem(last=False)


def clear_diff_preview_cache() -> None:
    """Clear in-memory diff preview cache."""
    _DIFF_PREVIEW_CACHE.clear()


def _resolve_repo_and_git_dir(path: Path, timeout_seconds: float) -> tuple[Path | None, Path | None]:
    """Resolve repository root and git-dir for a path."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel", "--git-dir"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
        )
    except Exception:
        return None, None
    if proc.returncode != 0:
        return None, None

    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        return None, None

    repo_root = Path(lines[0]).resolve()
    git_dir_raw = Path(lines[1])
    git_dir = git_dir_raw if git_dir_raw.is_absolute() else (repo_root / git_dir_raw)
    return repo_root, git_dir.resolve()


def _run_git(repo_root: Path, args: list[str], timeout_seconds: float) -> subprocess.CompletedProcess[str] | None:
    """Execute a git subcommand with timeout and tolerant failure handling."""
    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout_seconds,
        )
    except Exception:
        return None


def _parse_diff_hunks(diff_text: str) -> list[DiffHunk]:
    """Parse unified diff text into hunk records."""
    hunks: list[DiffHunk] = []
    current: DiffHunk | None = None

    for raw_line in diff_text.splitlines():
        match = _HUNK_RE.match(raw_line)
        if match:
            if current is not None:
                hunks.append(current)
            old_count = int(match.group(2) or "1")
            new_count = int(match.group(4) or "1")
            current = DiffHunk(
                old_start=int(match.group(1)),
                old_count=old_count,
                new_start=int(match.group(3)),
                new_count=new_count,
                removed_lines=[],
            )
            continue

        if current is None:
            continue
        if raw_line.startswith("-") and not raw_line.startswith("--- "):
            current.removed_lines.append(raw_line[1:])

    if current is not None:
        hunks.append(current)
    return hunks


def _format_marked_line(marker: str, code_line: str, colorize: bool) -> str:
    """Format one annotated preview line with marker-aware background styling."""
    if not colorize:
        return f"{marker} {code_line}"

    if marker == "+":
        return _apply_line_background(code_line, _ADDED_BG_SGR)
    if marker == "-":
        return _apply_line_background(code_line, _REMOVED_BG_SGR)
    return code_line


def _boost_foreground_contrast_for_diff(params: str) -> str:
    """Adjust low-contrast foreground SGR params for diff background readability."""
    parts = [part for part in params.split(";") if part]
    if not parts:
        return params

    boosted: list[str] = []
    index = 0
    while index < len(parts):
        token = parts[index]

        if token in {"38", "48"} and index + 1 < len(parts):
            mode = parts[index + 1]
            if mode == "5" and index + 2 < len(parts):
                color_token = parts[index + 2]
                if token == "38":
                    try:
                        color_index = int(color_token)
                    except ValueError:
                        color_index = -1
                    if 232 <= color_index <= 248:
                        boosted.extend(["38", "5", _DIFF_CONTRAST_8BIT])
                        index += 3
                        continue
                boosted.extend([token, "5", color_token])
                index += 3
                continue
            if mode == "2" and index + 4 < len(parts):
                red_token = parts[index + 2]
                green_token = parts[index + 3]
                blue_token = parts[index + 4]
                if token == "38":
                    try:
                        red = int(red_token)
                        green = int(green_token)
                        blue = int(blue_token)
                    except ValueError:
                        red = green = blue = -1
                    if (
                        red >= 0
                        and green >= 0
                        and blue >= 0
                        and abs(red - green) <= 8
                        and abs(green - blue) <= 8
                        and max(red, green, blue) < 190
                    ):
                        boosted.extend(["38", "2", *_DIFF_CONTRAST_TRUECOLOR])
                        index += 5
                        continue
                boosted.extend([token, "2", red_token, green_token, blue_token])
                index += 5
                continue

        # Drop faint text attribute so dim tokens stay legible on diff backgrounds.
        if token == "2":
            index += 1
            continue

        if token in {"30", "90"}:
            boosted.extend(["38", "5", _DIFF_CONTRAST_8BIT])
            index += 1
            continue

        boosted.append(token)
        index += 1

    return ";".join(boosted)


def _apply_line_background(code_line: str, bg_sgr: str) -> str:
    """Apply persistent background SGR to an ANSI-coded source line."""
    def _inject_bg(match: re.Match[str]) -> str:
        """Inject background into each SGR sequence while preserving contrast."""
        params = _boost_foreground_contrast_for_diff(match.group(1))
        if params:
            return f"\033[{params};{bg_sgr}m"
        return f"\033[{bg_sgr}m"

    line_with_persistent_bg = _SGR_RE.sub(_inject_bg, code_line)
    return f"\033[{bg_sgr}m{line_with_persistent_bg}\033[K\033[0m"


def _colorize_lines(lines: list[str], target: Path, style: str, colorize: bool) -> list[str]:
    """Colorize source lines and preserve one-to-one line count."""
    if not colorize or not lines:
        return lines
    rendered = colorize_source("\n".join(lines), target, style)
    rendered_lines = rendered.splitlines()
    if len(rendered_lines) != len(lines):
        return lines
    return rendered_lines


def _build_annotated_source_preview(
    source_lines: list[str],
    source_display_lines: list[str],
    hunks: list[DiffHunk],
    target: Path,
    style: str,
    colorize: bool,
) -> str:
    """Build combined source+diff view with inline added/removed markers."""
    total_lines = len(source_lines)
    added_line_numbers: set[int] = set()
    removed_insertions: dict[int, list[str]] = {}

    for hunk in hunks:
        for line_no in range(hunk.new_start, hunk.new_start + hunk.new_count):
            if 1 <= line_no <= total_lines:
                added_line_numbers.add(line_no)
        if hunk.removed_lines:
            insert_at = max(1, min(hunk.new_start, total_lines + 1))
            removed_insertions.setdefault(insert_at, []).extend(hunk.removed_lines)

    removed_display_insertions: dict[int, list[str]] = {}
    for insert_at, removed_lines in removed_insertions.items():
        removed_display_insertions[insert_at] = _colorize_lines(
            removed_lines,
            target,
            style,
            colorize,
        )

    output_lines: list[str] = []
    for line_no in range(1, total_lines + 1):
        for removed_line in removed_display_insertions.get(line_no, []):
            output_lines.append(_format_marked_line("-", removed_line, colorize))

        marker = "+" if line_no in added_line_numbers else " "
        display_line = (
            source_display_lines[line_no - 1]
            if line_no - 1 < len(source_display_lines)
            else source_lines[line_no - 1]
        )
        output_lines.append(_format_marked_line(marker, display_line, colorize))

    for removed_line in removed_display_insertions.get(total_lines + 1, []):
        output_lines.append(_format_marked_line("-", removed_line, colorize))

    return "\n".join(output_lines)


def build_unified_diff_preview_for_path(
    target: Path,
    timeout_seconds: float = 0.2,
    colorize: bool = True,
    style: str = "monokai",
) -> str | None:
    """Return annotated git diff preview for a tracked modified file.

    Returns ``None`` when target is outside a repo, unmodified/untracked, or no
    hunk data can be derived.
    """
    target = target.resolve()
    if not target.is_file():
        return None

    repo_root, git_dir = _resolve_repo_and_git_dir(target.parent, timeout_seconds)
    if repo_root is None or git_dir is None:
        return None
    if not target.is_relative_to(repo_root):
        return None

    rel_path = target.relative_to(repo_root)
    try:
        st = target.stat()
        mtime_ns = int(st.st_mtime_ns)
        size = int(st.st_size)
    except Exception:
        mtime_ns = 0
        size = 0

    git_signature = build_git_watch_signature(git_dir)
    cache_key = (str(target), mtime_ns, size, git_signature, bool(colorize), style)
    found, cached = _cache_get(cache_key)
    if found:
        return cached

    status_proc = _run_git(
        repo_root,
        ["status", "--porcelain=v1", "--untracked-files=normal", "--", str(rel_path)],
        timeout_seconds,
    )
    if status_proc is None or status_proc.returncode != 0:
        _cache_put(cache_key, None)
        return None

    status_line = next((line for line in status_proc.stdout.splitlines() if line), "")
    if not status_line or status_line.startswith("??"):
        _cache_put(cache_key, None)
        return None

    diff_proc = _run_git(
        repo_root,
        ["diff", "--no-color", "-U0", "HEAD", "--", str(rel_path)],
        timeout_seconds,
    )
    diff_text = diff_proc.stdout if diff_proc is not None and diff_proc.returncode == 0 else ""
    if not diff_text:
        staged_proc = _run_git(
            repo_root,
            ["diff", "--cached", "--no-color", "-U0", "--", str(rel_path)],
            timeout_seconds,
        )
        unstaged_proc = _run_git(
            repo_root,
            ["diff", "--no-color", "-U0", "--", str(rel_path)],
            timeout_seconds,
        )
        staged_text = staged_proc.stdout if staged_proc is not None and staged_proc.returncode == 0 else ""
        unstaged_text = unstaged_proc.stdout if unstaged_proc is not None and unstaged_proc.returncode == 0 else ""
        diff_text = staged_text or unstaged_text

    diff_text = sanitize_terminal_text(diff_text)
    hunks = _parse_diff_hunks(diff_text)
    if not hunks:
        _cache_put(cache_key, None)
        return None

    source = sanitize_terminal_text(read_text(target))
    source_lines = source.splitlines()
    source_display_lines = _colorize_lines(source_lines, target, style, colorize)
    rendered = _build_annotated_source_preview(
        source_lines,
        source_display_lines,
        hunks,
        target,
        style,
        colorize,
    )
    _cache_put(cache_key, rendered)
    return rendered
