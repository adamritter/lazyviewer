"""Top-of-file one-line summary extraction with small metadata cache."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
import re
import threading

DOC_SUMMARY_READ_BYTES = 4_096
DOC_SUMMARY_MAX_FILE_BYTES = 256 * 1024
DOC_SUMMARY_MAX_CHARS = 96
DOC_SUMMARY_CACHE_MAX = 4_096

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_CODING_COOKIE_RE = re.compile(r"^#.*coding[:=]\s*[-\w.]+")
_TRIPLE_QUOTE_PREFIXES = ('"""', "'''")
_LINE_COMMENT_PREFIXES = ("#", "//", "--", ";")
_DOC_SUMMARY_CACHE: OrderedDict[tuple[str, int, int], str | None] = OrderedDict()
_DOC_SUMMARY_CACHE_LOCK = threading.RLock()
_CACHE_MISS = object()


def _sanitize_terminal_text(source: str) -> str:
    """Escape terminal control bytes to avoid side effects."""
    if _CONTROL_RE.search(source) is None:
        return source

    out: list[str] = []
    for ch in source:
        code = ord(ch)
        if ch in {"\n", "\r", "\t"}:
            out.append(ch)
            continue
        if code < 32 or code == 127 or 0x80 <= code <= 0x9F:
            out.append(f"\\x{code:02x}")
            continue
        out.append(ch)
    return "".join(out)


def _normalize_doc_summary(text: str) -> str | None:
    """Normalize doc text to one safe short line."""
    candidate = _sanitize_terminal_text(" ".join(text.strip().split()))
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


def top_file_doc_summary(path: Path, size_bytes: int | None) -> str | None:
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


def _doc_summary_cache_key(path: Path, size_bytes: int | None) -> tuple[str, int, int] | None:
    """Build cache key from resolved path, mtime, and size."""
    try:
        resolved = path.resolve()
        stat = resolved.stat()
    except Exception:
        return None
    size = int(size_bytes) if size_bytes is not None else int(stat.st_size)
    return str(resolved), int(stat.st_mtime_ns), size


def cached_top_file_doc_summary(path: Path, size_bytes: int | None) -> str | None:
    """Return cached top-of-file summary for ``path`` when possible."""
    cache_key = _doc_summary_cache_key(path, size_bytes)
    if cache_key is not None:
        with _DOC_SUMMARY_CACHE_LOCK:
            cached = _DOC_SUMMARY_CACHE.get(cache_key, _CACHE_MISS)
            if cached is not _CACHE_MISS:
                _DOC_SUMMARY_CACHE.move_to_end(cache_key)
                return cached

    summary = top_file_doc_summary(path, size_bytes)

    if cache_key is not None:
        with _DOC_SUMMARY_CACHE_LOCK:
            _DOC_SUMMARY_CACHE[cache_key] = summary
            _DOC_SUMMARY_CACHE.move_to_end(cache_key)
            while len(_DOC_SUMMARY_CACHE) > DOC_SUMMARY_CACHE_MAX:
                _DOC_SUMMARY_CACHE.popitem(last=False)

    return summary


def clear_doc_summary_cache() -> None:
    """Clear in-memory top-of-file summary cache."""
    with _DOC_SUMMARY_CACHE_LOCK:
        _DOC_SUMMARY_CACHE.clear()


__all__ = [
    "top_file_doc_summary",
    "cached_top_file_doc_summary",
    "clear_doc_summary_cache",
]
