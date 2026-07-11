"""Thread-safe top-of-file summary extraction owned by the workspace model."""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from pathlib import Path

DOC_SUMMARY_READ_BYTES = 4_096
DOC_SUMMARY_MAX_FILE_BYTES = 256 * 1024
DOC_SUMMARY_MAX_CHARS = 96
DOC_SUMMARY_CACHE_MAX = 4_096

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_CODING_COOKIE_RE = re.compile(r"^#.*coding[:=]\s*[-\w.]+")
_TRIPLE_QUOTE_PREFIXES = ('"""', "'''")
_LINE_COMMENT_PREFIXES = ("#", "//", "--", ";")
_CACHE: OrderedDict[tuple[str, int, int], str | None] = OrderedDict()
_LOCK = threading.RLock()
_MISS = object()


def _sanitize(text: str) -> str:
    if _CONTROL_RE.search(text) is None:
        return text
    out: list[str] = []
    for character in text:
        code = ord(character)
        if character in {"\n", "\r", "\t"}:
            out.append(character)
        elif code < 32 or code == 127 or 0x80 <= code <= 0x9F:
            out.append(f"\\x{code:02x}")
        else:
            out.append(character)
    return "".join(out)


def _normalize(text: str) -> str | None:
    candidate = _sanitize(" ".join(text.strip().split()))
    if not candidate:
        return None
    if len(candidate) > DOC_SUMMARY_MAX_CHARS:
        return candidate[: DOC_SUMMARY_MAX_CHARS - 3].rstrip() + "..."
    return candidate


def _triple_quote_summary(lines: list[str], start: int, delimiter: str) -> str | None:
    body = lines[start].lstrip()[len(delimiter) :]
    if delimiter in body:
        return _normalize(body.split(delimiter, 1)[0])
    first = _normalize(body)
    if first:
        return first
    for line in lines[start + 1 :]:
        if delimiter in line:
            return _normalize(line.split(delimiter, 1)[0])
        candidate = _normalize(line)
        if candidate:
            return candidate
    return None


def _block_comment_summary(lines: list[str], start: int) -> str | None:
    body = lines[start].lstrip()[2:]
    if "*/" in body:
        return _normalize(body.split("*/", 1)[0].lstrip("*").strip())
    first = _normalize(body.lstrip("*").strip())
    if first:
        return first
    for line in lines[start + 1 :]:
        if "*/" in line:
            return _normalize(line.split("*/", 1)[0].lstrip("*").strip())
        candidate = _normalize(line.lstrip("*").strip())
        if candidate:
            return candidate
    return None


def _line_comment_summary(lines: list[str], start: int, prefix: str) -> str | None:
    for line in lines[start:]:
        stripped = line.lstrip()
        if not stripped:
            continue
        if not stripped.startswith(prefix):
            break
        candidate = _normalize(stripped[len(prefix) :])
        if candidate:
            return candidate
    return None


def top_file_doc_summary(path: Path, size_bytes: int | None) -> str | None:
    """Return a safe one-line summary from a small top-of-file sample."""
    if size_bytes is not None and size_bytes > DOC_SUMMARY_MAX_FILE_BYTES:
        return None
    try:
        with path.open("rb") as handle:
            sample = handle.read(DOC_SUMMARY_READ_BYTES)
    except OSError:
        return None
    if not sample or b"\x00" in sample:
        return None
    lines = sample.decode("utf-8", errors="replace").splitlines()
    if not lines:
        return None
    lines[0] = lines[0].lstrip("\ufeff")
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index < len(lines) and lines[index].lstrip().startswith("#!"):
        index += 1
    if index < len(lines) and _CODING_COOKIE_RE.match(lines[index].strip()):
        index += 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    if index >= len(lines):
        return None

    first = lines[index].lstrip()
    for delimiter in _TRIPLE_QUOTE_PREFIXES:
        if first.startswith(delimiter):
            return _triple_quote_summary(lines, index, delimiter)
    if first.startswith("/*"):
        return _block_comment_summary(lines, index)
    for prefix in _LINE_COMMENT_PREFIXES:
        if first.startswith(prefix):
            return _line_comment_summary(lines, index, prefix)
    return None


def _cache_key(path: Path, size_bytes: int | None) -> tuple[str, int, int] | None:
    try:
        resolved = path.resolve()
        stat = resolved.stat()
    except OSError:
        return None
    size = int(size_bytes) if size_bytes is not None else int(stat.st_size)
    return str(resolved), int(stat.st_mtime_ns), size


def cached_top_file_doc_summary(path: Path, size_bytes: int | None) -> str | None:
    key = _cache_key(path, size_bytes)
    if key is not None:
        with _LOCK:
            cached = _CACHE.get(key, _MISS)
            if cached is not _MISS:
                _CACHE.move_to_end(key)
                return cached
    summary = top_file_doc_summary(path, size_bytes)
    if key is not None:
        with _LOCK:
            _CACHE[key] = summary
            _CACHE.move_to_end(key)
            while len(_CACHE) > DOC_SUMMARY_CACHE_MAX:
                _CACHE.popitem(last=False)
    return summary


def clear_doc_summary_cache() -> None:
    with _LOCK:
        _CACHE.clear()
