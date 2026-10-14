"""Bounded reads for interactive previews of large text/log files."""
from __future__ import annotations

import codecs
from pathlib import Path

from .syntax import sanitize_terminal_text

PAGE_BYTES = 32 * 1024


class PagedText(str):
    """Load more source only as navigation reaches the loaded portion."""

    def __new__(cls, path: Path, size: int, style: str | None = None):
        with path.open("rb") as stream:
            sample = stream.read(PAGE_BYTES)
        decoder = codecs.getincrementaldecoder("utf-8")("strict")
        try:
            source = decoder.decode(sample, final=len(sample) >= size)
        except UnicodeDecodeError:
            decoder = codecs.getincrementaldecoder("latin-1")("replace")
            source = decoder.decode(sample, final=len(sample) >= size)
        decoder.errors = "replace"
        instance = super().__new__(cls, sanitize_terminal_text(source))
        instance.style = style
        instance.path = path
        instance.file_size = size
        instance.window_start = 0
        instance.loaded_bytes = len(sample)
        instance._decoder = decoder
        instance._source = str(instance)
        instance._layout = (80, False)
        instance._signature = instance._stat_signature()
        return instance

    def _stat_signature(self):
        st = self.path.stat()
        return st.st_ino, st.st_mtime_ns, st.st_size

    @property
    def progress_label(self):
        if self.window_start:
            return f"[Bytes {self.window_start:,}-{self.loaded_bytes:,} / {self.file_size:,}]"
        return "" if self.complete else f"[Loaded {self.loaded_bytes:,} / {self.file_size:,} bytes]"

    @property
    def complete(self):
        return self.loaded_bytes >= self.file_size

    def grow(self):
        if self.complete:
            return False
        signature = self._stat_signature()
        if signature != self._signature:
            # Never concatenate bytes from two different versions of a log.
            self.window_start = 0
            self.loaded_bytes = 0
            self._source = ""
            self._decoder.reset()
            self.file_size = signature[2]
            self._signature = signature
        with self.path.open("rb") as stream:
            stream.seek(self.loaded_bytes)
            sample = stream.read(PAGE_BYTES)
        self.loaded_bytes += len(sample)
        if not sample:
            self.file_size = self.loaded_bytes
        self._source += sanitize_terminal_text(self._decoder.decode(sample, final=self.complete))
        return bool(sample)

    def seek_start(self):
        if not self.window_start:
            return False
        fresh = type(self)(self.path, self.path.stat().st_size, self.style)
        self.__dict__.update(fresh.__dict__)
        return True

    def seek_end(self):
        size = self.path.stat().st_size
        if self.complete and size == self.file_size:
            return False
        offset = max(0, size-PAGE_BYTES)
        with self.path.open("rb") as stream:
            stream.seek(offset)
            sample = stream.read(PAGE_BYTES)
        if offset:
            newline = sample.find(b"\n")
            if newline >= 0:
                offset += newline+1
                sample = sample[newline+1:]
        self.window_start = offset
        self.loaded_bytes = size
        self.file_size = size
        self._decoder.reset()
        self._source = sanitize_terminal_text(self._decoder.decode(sample, final=True))
        self._signature = self._stat_signature()
        return True

    def splitlines(self, keepends=False):
        return self._source.splitlines(keepends)

    def screen_lines(self, width: int, wrap: bool = False):
        from ..render.ansi import build_screen_lines
        self._layout = (width, wrap)
        text = self._source
        if not self.complete:
            text += f"\n\n\033[2m[Loaded {self.loaded_bytes:,} / {self.file_size:,} bytes; scroll to load more]\033[0m"
        if self.style is not None:
            from .lazy_syntax import LazyText, lazy_source
            document = lazy_source(self._source, self.path, self.style)
            base_lines = text.splitlines(keepends=True) or [""]
            count = len(self._source.splitlines(keepends=True)) or 1
            def region(index, limit, deadline=float("inf")):
                if index >= count:
                    return base_lines[index]
                rendered = document.render_region(index, limit, deadline)
                if rendered is None:
                    return None
                if base_lines[index].endswith("\n") and not rendered.endswith("\n"):
                    rendered += "\n"
                return rendered
            text = LazyText(text, lambda index: region(index, None), region)
        return build_screen_lines(text, width, wrap)

    def refreshed_lines(self):
        return self.screen_lines(*self._layout)
