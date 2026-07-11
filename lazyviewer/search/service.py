"""Search engine with revision-keyed caches and typed streaming jobs."""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Empty, Queue
from typing import Protocol

from ..workspace import WorkspaceService
from .content import ContentMatch, search_project_content_rg
from .fuzzy import fuzzy_match_label_index
from .model import (
    ContentMatchesAdded,
    ContentSearchFinished,
    ContentSearchRequest,
    ContentSearchResult,
    ContentSearchUpdate,
    FileSearchMatch,
    FileSearchRequest,
)

RootSearchResult = tuple[dict[Path, list[ContentMatch]], bool, str | None]
MatchCallback = Callable[[Path, ContentMatch, int, int], None]
CancelProbe = Callable[[], bool]


class ContentSearchBackend(Protocol):
    def __call__(
        self,
        root: Path,
        query: str,
        show_hidden: bool,
        skip_gitignored: bool = False,
        max_matches: int = 2_000,
        max_files: int = 500,
        on_match: MatchCallback | None = None,
        should_cancel: CancelProbe | None = None,
    ) -> RootSearchResult: ...


class ContentSearchJob:
    """Cancelable streaming handle returned by :class:`SearchService`."""

    def __init__(self, request: ContentSearchRequest) -> None:
        self.request = request
        self._cancelled = threading.Event()
        self._updates: Queue[ContentSearchUpdate] = Queue()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self) -> None:
        self._cancelled.set()

    def poll(self, timeout_seconds: float = 0.0) -> tuple[ContentSearchUpdate, ...]:
        updates: list[ContentSearchUpdate] = []
        if timeout_seconds > 0:
            try:
                updates.append(self._updates.get(timeout=timeout_seconds))
            except Empty:
                pass
        while True:
            try:
                updates.append(self._updates.get_nowait())
            except Empty:
                return tuple(updates)

    def publish(self, update: ContentSearchUpdate) -> None:
        if not self.cancelled:
            self._updates.put(update)


class SearchService:
    """Own file matching, content workers, and revision-scoped result caching."""

    def __init__(
        self,
        workspace: WorkspaceService,
        *,
        content_backend: ContentSearchBackend | None = None,
        cache_size: int = 64,
    ) -> None:
        self.workspace = workspace
        self._content_backend = content_backend or search_project_content_rg
        self._cache_size = max(1, cache_size)
        self._content_cache: OrderedDict[
            tuple[str, str, bool, bool, int, int], ContentSearchResult
        ] = OrderedDict()
        self._cache_lock = threading.RLock()

    def match_files(self, request: FileSearchRequest) -> tuple[FileSearchMatch, ...]:
        index = self.workspace.file_index(request.workspace)
        matched = fuzzy_match_label_index(
            request.query,
            list(index.labels),
            limit=max(1, request.limit),
        )
        return tuple(
            FileSearchMatch(
                path=index.paths[position],
                label=label,
                section=index.sections[position],
                score=score,
            )
            for position, label, score in matched
        )

    def cached_content(self, request: ContentSearchRequest) -> ContentSearchResult | None:
        with self._cache_lock:
            result = self._content_cache.get(request.cache_key)
            if result is not None:
                self._content_cache.move_to_end(request.cache_key)
            return result

    def _store_content(self, request: ContentSearchRequest, result: ContentSearchResult) -> None:
        with self._cache_lock:
            self._content_cache[request.cache_key] = result
            self._content_cache.move_to_end(request.cache_key)
            while len(self._content_cache) > self._cache_size:
                self._content_cache.popitem(last=False)

    def start_content(self, request: ContentSearchRequest) -> ContentSearchJob:
        job = ContentSearchJob(request)
        cached = self.cached_content(request)
        if cached is not None:
            job.publish(ContentSearchFinished(cached))
            return job
        threading.Thread(
            target=self._run_content_job,
            args=(job,),
            name=f"lazyviewer-content-search-{request.workspace.revision.tree[:8]}",
            daemon=True,
        ).start()
        return job

    def _run_content_job(self, job: ContentSearchJob) -> None:
        request = job.request
        roots = request.workspace.query.roots
        if not roots or not request.query:
            result = ContentSearchResult((), False, None)
            self._store_content(request, result)
            job.publish(ContentSearchFinished(result))
            return

        seen_matches: set[tuple[str, int, int, str]] = set()
        seen_files: set[str] = set()
        seen_lock = threading.Lock()
        streamed_count = 0
        stream_truncated = threading.Event()
        local_cancel = threading.Event()

        def cancelled() -> bool:
            return job.cancelled or local_cancel.is_set()

        def emit(path: Path, match: ContentMatch) -> None:
            nonlocal streamed_count
            resolved = path.resolve()
            key = (str(resolved), match.line, match.column, match.preview)
            with seen_lock:
                if key in seen_matches or cancelled():
                    return
                if streamed_count >= max(1, request.max_matches):
                    stream_truncated.set()
                    local_cancel.set()
                    return
                file_key = str(resolved)
                if file_key not in seen_files and len(seen_files) >= max(1, request.max_files):
                    stream_truncated.set()
                    local_cancel.set()
                    return
                seen_matches.add(key)
                seen_files.add(file_key)
                streamed_count += 1
            job.publish(ContentMatchesAdded((match,)))

        def search_root(root: Path) -> RootSearchResult:
            def on_match(path: Path, match: ContentMatch, _matches: int, _files: int) -> None:
                emit(path, match)

            return self._content_backend(
                root,
                request.query,
                request.workspace.query.show_hidden,
                skip_gitignored=request.workspace.query.skip_gitignored,
                max_matches=max(1, request.max_matches),
                max_files=max(1, request.max_files),
                on_match=on_match,
                should_cancel=cancelled,
            )

        if len(roots) == 1:
            root_results = [search_root(roots[0])]
        else:
            root_results: list[RootSearchResult] = [({}, False, None) for _ in roots]
            with ThreadPoolExecutor(
                max_workers=min(8, len(roots)),
                thread_name_prefix="lazyviewer-content-search",
            ) as executor:
                futures = [executor.submit(search_root, root) for root in roots]
                for section, future in enumerate(futures):
                    try:
                        root_results[section] = future.result()
                    except Exception as exc:
                        root_results[section] = ({}, False, f"failed to search: {exc}")
        if job.cancelled:
            return
        result = self._merge_results(
            roots,
            root_results,
            max_matches=max(1, request.max_matches),
            max_files=max(1, request.max_files),
            stream_truncated=stream_truncated.is_set(),
        )
        self._store_content(request, result)
        job.publish(ContentSearchFinished(result))

    @staticmethod
    def _merge_results(
        roots: tuple[Path, ...],
        root_results: list[RootSearchResult],
        *,
        max_matches: int,
        max_files: int,
        stream_truncated: bool,
    ) -> ContentSearchResult:
        merged: dict[Path, list[ContentMatch]] = {}
        seen: set[tuple[str, int, int, str]] = set()
        errors: list[str] = []
        truncated = stream_truncated
        total = 0
        for section, (matches, root_truncated, error) in enumerate(root_results):
            if error:
                errors.append(f"{roots[section]}: {error}")
            truncated = truncated or root_truncated
            for path in sorted(matches, key=lambda value: str(value).casefold()):
                resolved = path.resolve()
                for match in matches[path]:
                    key = (str(resolved), match.line, match.column, match.preview)
                    if key in seen:
                        continue
                    if total >= max_matches or (resolved not in merged and len(merged) >= max_files):
                        truncated = True
                        break
                    seen.add(key)
                    merged.setdefault(resolved, []).append(match)
                    total += 1
        for path in merged:
            merged[path].sort(key=lambda item: (item.line, item.column, item.preview))
        return ContentSearchResult.create(
            merged,
            truncated,
            errors[0] if not merged and errors else None,
        )
