"""Gitignore-aware path filtering utilities.

Builds a matcher by querying git for ignored files and directories.
Tree/index builders use this to optionally hide ignored content.
"""

from __future__ import annotations

from collections import OrderedDict
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


GITIGNORE_MATCHER_CACHE_MAX = 64
GITIGNORE_MATCHER_CACHE_TTL_SECONDS = 2.0


@dataclass(frozen=True)
class _MatcherCacheEntry:
    """Cached matcher plus root directory mtime and insertion timestamp."""

    matcher: GitIgnoreMatcher | None
    root_mtime_ns: int | None
    loaded_at: float


_GITIGNORE_MATCHER_CACHE: OrderedDict[str, _MatcherCacheEntry] = OrderedDict()


def clear_gitignore_cache() -> None:
    """Clear cached gitignore matchers."""
    _GITIGNORE_MATCHER_CACHE.clear()


def _is_within(path: Path, root: Path) -> bool:
    """Return whether ``path`` is at or under ``root`` after resolution."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class GitIgnoreMatcher:
    """Resolved gitignore snapshot for a project subtree.

    ``ignored_dirs`` is stored as resolved directory paths so parent checks can
    quickly reject whole subtrees.
    """

    root: Path
    ignored_files: frozenset[Path]
    ignored_dirs: frozenset[Path]

    def is_ignored(self, path: Path) -> bool:
        """Return whether ``path`` is ignored under this matcher root."""
        resolved = path.resolve()
        if not _is_within(resolved, self.root):
            return False
        if resolved in self.ignored_files:
            return True
        current = resolved
        while True:
            if current in self.ignored_dirs:
                return True
            if current == self.root:
                break
            parent = current.parent
            if parent == current:
                break
            current = parent
        return False


def _load_matcher(root: Path) -> GitIgnoreMatcher | None:
    """Build a matcher by querying git for ignored files/directories.

    Returns ``None`` when git is unavailable, ``root`` is not inside a repo, or
    any probing command fails. The matcher only tracks ignored paths within
    ``root`` (even when the git repo root is higher).
    """
    if shutil.which("git") is None:
        return None

    root = root.resolve()
    try:
        top_proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=True,
        )
    except Exception:
        return None

    top_level = top_proc.stdout.strip()
    if not top_level:
        return None

    repo_root = Path(top_level).resolve()
    if not _is_within(root, repo_root):
        return None

    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "ls-files",
                "-z",
                "--others",
                "-i",
                "--exclude-standard",
                "--directory",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except Exception:
        return None

    ignored_files: set[Path] = set()
    ignored_dirs: set[Path] = set()
    for raw in proc.stdout.split(b"\x00"):
        if not raw:
            continue
        rel = raw.decode("utf-8", errors="replace")
        is_dir = rel.endswith("/")
        rel = rel.rstrip("/")
        if not rel:
            continue
        abs_path = (repo_root / rel).resolve()
        if not _is_within(abs_path, root):
            continue
        if is_dir:
            ignored_dirs.add(abs_path)
            continue
        if abs_path.is_dir():
            ignored_dirs.add(abs_path)
        else:
            ignored_files.add(abs_path)

    return GitIgnoreMatcher(
        root=root,
        ignored_files=frozenset(ignored_files),
        ignored_dirs=frozenset(ignored_dirs),
    )


def get_gitignore_matcher(root: Path) -> GitIgnoreMatcher | None:
    """Return cached matcher for ``root`` with bounded staleness."""
    resolved_root = root.resolve()
    key = str(resolved_root)
    try:
        root_mtime_ns: int | None = int(resolved_root.stat().st_mtime_ns)
    except Exception:
        root_mtime_ns = None
    now = time.monotonic()

    cached = _GITIGNORE_MATCHER_CACHE.get(key)
    if cached is not None:
        cache_age = now - cached.loaded_at
        if (
            cached.root_mtime_ns == root_mtime_ns
            and cache_age <= GITIGNORE_MATCHER_CACHE_TTL_SECONDS
        ):
            _GITIGNORE_MATCHER_CACHE.move_to_end(key)
            return cached.matcher

    matcher = _load_matcher(resolved_root)
    _GITIGNORE_MATCHER_CACHE[key] = _MatcherCacheEntry(
        matcher=matcher,
        root_mtime_ns=root_mtime_ns,
        loaded_at=now,
    )
    _GITIGNORE_MATCHER_CACHE.move_to_end(key)
    while len(_GITIGNORE_MATCHER_CACHE) > GITIGNORE_MATCHER_CACHE_MAX:
        _GITIGNORE_MATCHER_CACHE.popitem(last=False)
    return matcher
