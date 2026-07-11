"""Present semantic preview documents for the terminal source pane."""

from __future__ import annotations

from pathlib import Path

from ..git_status import format_git_status_badges
from ..preview import (
    BinaryDocument,
    COLORIZE_MAX_FILE_BYTES,
    DiffDocument,
    DirectoryDocument,
    ErrorDocument,
    ImageDocument,
    PreviewDocument,
    RenderedPreview,
    TextDocument,
)
from ..tree_model.rendering import TREE_SIZE_LABEL_MIN_BYTES, file_color_for
from ..ui_theme import UITheme
from .diff import _ADDED_BG_SGR, _REMOVED_BG_SGR, _apply_line_background
from .syntax import colorize_source


def _badges(path: Path, flags: int, theme: UITheme) -> str:
    if not flags:
        return ""
    return format_git_status_badges(path, {path.resolve(): flags}, theme=theme)


def _present_directory(document: DirectoryDocument, theme: UITheme) -> str:
    root = document.path
    lines = [
        f"{theme.tree_dir}{root.resolve()}/{theme.reset}"
        f"{_badges(root, document.git_status_flags, theme)}",
        "",
    ]
    for row in document.rows:
        prefix = "".join("   " if ancestor_is_last else "│  " for ancestor_is_last in row.ancestor_last)
        branch = "└─ " if row.is_last else "├─ "
        if row.error is not None:
            lines.append(
                f"{theme.divider}{prefix}{branch}{theme.reset}"
                f"{theme.help_dim}<error: {row.error}>{theme.reset}"
            )
            continue
        suffix = "/" if row.is_dir else ""
        name_color = theme.tree_dir if row.is_dir else file_color_for(row.path, theme)
        size_label = ""
        if (
            not row.is_dir
            and row.file_size is not None
            and row.file_size >= TREE_SIZE_LABEL_MIN_BYTES
        ):
            size_label = f"{theme.tree_size} [{row.file_size // 1024} KB]{theme.reset}"
        doc_label = (
            f"{theme.help_dim}  -- {row.doc_summary}{theme.reset}"
            if row.doc_summary
            else ""
        )
        lines.append(
            f"{theme.divider}{prefix}{branch}{theme.reset}"
            f"{name_color}{row.path.name}{suffix}{theme.reset}"
            f"{size_label}{_badges(row.path, row.git_status_flags, theme)}{doc_label}"
        )
    if document.truncated:
        lines.extend(
            [
                "",
                f"{theme.help_dim}... truncated after {document.max_entries} entries ...{theme.reset}",
            ]
        )
    return "\n".join(lines)


def _colorized_diff_lines(document: DiffDocument, style: str) -> list[str]:
    raw = [line.text for line in document.lines]
    if not raw:
        return []
    rendered = colorize_source("\n".join(raw), document.path, style).splitlines()
    return rendered if len(rendered) == len(raw) else raw


def _present_diff(document: DiffDocument, style: str, color: bool) -> str:
    display = _colorized_diff_lines(document, style) if color else [line.text for line in document.lines]
    output: list[str] = []
    for semantic, rendered in zip(document.lines, display, strict=True):
        if not color:
            marker = {"added": "+", "removed": "-"}.get(semantic.kind, " ")
            output.append(f"{marker} {rendered}")
        elif semantic.kind == "added":
            output.append(_apply_line_background(rendered, _ADDED_BG_SGR))
        elif semantic.kind == "removed":
            output.append(_apply_line_background(rendered, _REMOVED_BG_SGR))
        else:
            output.append(rendered)
    return "\n".join(output)


class PreviewPresenter:
    """Terminal presentation policy kept outside filesystem loading."""

    def __init__(self, *, style: str, color: bool, theme: UITheme) -> None:
        self.style = style
        self.color = color
        self.theme = theme

    def present(self, document: PreviewDocument) -> RenderedPreview:
        if isinstance(document, DirectoryDocument):
            text = _present_directory(document, self.theme)
        elif isinstance(document, ImageDocument):
            text = f"{document.path}\n\n<PNG image preview via Kitty graphics protocol>"
        elif isinstance(document, BinaryDocument):
            size = f": {document.file_size} bytes" if document.file_size >= 0 else ""
            text = f"{document.path}\n\n<binary file{size}>"
        elif isinstance(document, ErrorDocument):
            text = f"{document.path}\n\n<error reading file: {document.message}>"
        elif isinstance(document, DiffDocument):
            text = _present_diff(document, self.style, self.color)
        elif isinstance(document, TextDocument):
            if self.color and document.file_size <= COLORIZE_MAX_FILE_BYTES:
                text = colorize_source(document.text, document.path, self.style)
            else:
                text = document.text
        else:  # pragma: no cover - the union is exhaustive at type-check time.
            raise TypeError(f"unsupported preview document: {type(document)!r}")
        return RenderedPreview(document=document, text=text)


__all__ = ["PreviewPresenter"]
