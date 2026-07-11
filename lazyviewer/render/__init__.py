"""Retained terminal renderer for lazyviewer's split-pane interface.

The public functions still build deterministic full ANSI frames for capture
tests.  ``RetainedPageRenderer`` additionally caches independently rendered
pane surfaces so the interactive runtime does not recompute an unchanged tree
while the preview scrolls.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .ansi import ANSI_ESCAPE_RE, char_display_width, clip_ansi_line
from .frame import Frame, Rect, Surface
from .help import (
    help_panel_lines,
    help_panel_row_count,
    render_help_page as render_help_page,
)
from ..source_pane.renderer import SourcePaneRenderer
from ..tree_model import TreeEntry, clamp_left_width
from ..tree_pane.rendering import TreePaneRenderer
from ..ui_theme import DEFAULT_THEME, UITheme


@dataclass
class RenderContext:
    """Input snapshot used to render one frame without further state mutation."""

    text_lines: list[str]
    text_start: int
    tree_entries: list[TreeEntry]
    tree_start: int
    tree_selected: int
    max_lines: int
    current_path: Path
    tree_root: Path
    expanded: set[Path]
    width: int
    left_width: int
    text_x: int
    wrap_text: bool
    browser_visible: bool
    show_hidden: bool
    tree_roots: list[Path] | None = None
    workspace_expanded: list[set[Path]] | None = None
    show_help: bool = False
    show_tree_sizes: bool = True
    status_message: str = ""
    tree_filter_active: bool = False
    tree_filter_row_visible: bool = True
    tree_filter_mode: str = "files"
    tree_filter_query: str = ""
    tree_filter_editing: bool = False
    tree_filter_cursor_visible: bool = False
    tree_filter_match_count: int = 0
    tree_filter_truncated: bool = False
    tree_filter_loading: bool = False
    tree_filter_spinner_frame: int = 0
    tree_filter_prefix: str = "p>"
    tree_filter_placeholder: str = "type to filter files"
    picker_active: bool = False
    picker_mode: str = "symbols"
    picker_query: str = ""
    picker_items: list[str] | None = None
    picker_selected: int = 0
    picker_focus: str = "query"
    picker_list_start: int = 0
    picker_message: str = ""
    git_status_overlay: dict[Path, int] | None = None
    tree_search_query: str = ""
    text_search_query: str = ""
    text_search_current_line: int = 0
    text_search_current_column: int = 0
    preview_is_git_diff: bool = False
    source_selection_anchor: tuple[int, int] | None = None
    source_selection_focus: tuple[int, int] | None = None
    theme: UITheme = DEFAULT_THEME


@dataclass(frozen=True, slots=True)
class RenderStats:
    """Cumulative retained-renderer work counters used for diagnostics."""

    tree_renders: int
    preview_renders: int
    help_renders: int


@dataclass(frozen=True, slots=True)
class _SourcePresentation:
    rows: tuple[str, ...]
    text_percent: float
    status_start: int
    status_end: int
    status_total: int


@dataclass(frozen=True, slots=True)
class _HelpPresentation:
    rows: int
    tree_lines: tuple[str, ...]
    text_lines: tuple[str, ...]
    text_only_lines: tuple[str, ...]


def _help_line(lines: tuple[str, ...], row: int) -> str:
    """Safe row lookup for help-line tuples."""
    if 0 <= row < len(lines):
        return lines[row]
    return ""


def build_status_line(left_text: str, width: int, right_text: str = "│ ? Help") -> str:
    """Build one-line status bar with right-aligned help hint."""
    usable = max(1, width)
    if usable <= len(right_text):
        return right_text[-usable:]
    left_limit = max(0, usable - len(right_text) - 1)
    left = left_text[:left_limit]
    gap = " " * (usable - len(left) - len(right_text))
    return f"{left}{gap}{right_text}"


def _compose_status_left_text(
    current_path: Path,
    status_start: int,
    status_end: int,
    status_total: int,
    text_percent: float,
    status_message: str,
) -> str:
    """Compose left status segment with location and transient message."""
    base = f"{current_path} ({status_start}-{status_end}/{status_total} {text_percent:5.1f}%)"
    if not status_message:
        return base
    return f"{base} · {status_message}"


def _container_identity(value: object) -> tuple[int, int]:
    """Return a cheap identity key while also noticing collection-size changes."""
    try:
        size = len(value)  # type: ignore[arg-type]
    except TypeError:
        size = -1
    return id(value), size


class RetainedPageRenderer:
    """Render and cache independently invalidated tree, preview, and help panes.

    View collections in ``RenderContext`` are replaced rather than mutated by
    the session controllers.  Identity keys therefore make cache checks O(1)
    even for very large workspaces.  The previous context is retained so an
    identity cannot be recycled while its cached surface is current.
    """

    def __init__(self, *, canonical_text: bool = True) -> None:
        self._canonical_text = canonical_text
        self._tree_key: tuple[object, ...] | None = None
        self._tree_rows: tuple[str, ...] = ()
        self._source_key: tuple[object, ...] | None = None
        self._source: _SourcePresentation | None = None
        self._help_key: tuple[object, ...] | None = None
        self._help: _HelpPresentation | None = None
        self._previous_context: RenderContext | None = None
        self._tree_renders = 0
        self._preview_renders = 0
        self._help_renders = 0

    @property
    def stats(self) -> RenderStats:
        """Return cumulative cache-miss counts."""
        return RenderStats(
            tree_renders=self._tree_renders,
            preview_renders=self._preview_renders,
            help_renders=self._help_renders,
        )

    def invalidate(self) -> None:
        """Drop all cached presentations before a forced logical rebuild."""
        self._tree_key = None
        self._tree_rows = ()
        self._source_key = None
        self._source = None
        self._help_key = None
        self._help = None
        self._previous_context = None

    def render(self, context: RenderContext) -> Frame:
        """Render a structured frame, reusing panes whose inputs are unchanged."""
        active_theme = context.theme or DEFAULT_THEME
        help_presentation = self._render_help(context, active_theme)
        content_rows = max(1, context.max_lines - help_presentation.rows)

        if context.browser_visible and context.width >= 3:
            frame = self._render_split(
                context,
                active_theme,
                help_presentation,
                content_rows,
            )
        else:
            frame = self._render_text_only(
                context,
                active_theme,
                help_presentation,
                content_rows,
            )

        self._previous_context = context
        return frame

    def _render_help(self, context: RenderContext, theme: UITheme) -> _HelpPresentation:
        key = (
            context.max_lines,
            context.show_help,
            context.browser_visible,
            context.tree_filter_active,
            context.tree_filter_mode,
            context.tree_filter_editing,
            theme,
        )
        if key == self._help_key and self._help is not None:
            return self._help

        tree_lines, text_lines, text_only_lines = help_panel_lines(
            theme=theme,
            tree_filter_active=context.tree_filter_active,
            tree_filter_mode=context.tree_filter_mode,
            tree_filter_editing=context.tree_filter_editing,
        )
        presentation = _HelpPresentation(
            rows=help_panel_row_count(
                context.max_lines,
                context.show_help,
                browser_visible=context.browser_visible,
                tree_filter_active=context.tree_filter_active,
                tree_filter_mode=context.tree_filter_mode,
                tree_filter_editing=context.tree_filter_editing,
            ),
            tree_lines=tree_lines,
            text_lines=text_lines,
            text_only_lines=text_only_lines,
        )
        self._help_key = key
        self._help = presentation
        self._help_renders += 1
        return presentation

    def _render_source(
        self,
        context: RenderContext,
        *,
        content_rows: int,
        line_width: int,
    ) -> _SourcePresentation:
        key = (
            _container_identity(context.text_lines),
            context.text_start,
            content_rows,
            line_width,
            context.current_path,
            context.wrap_text,
            context.text_x,
            context.text_search_query,
            context.text_search_current_line,
            context.text_search_current_column,
            context.preview_is_git_diff,
            context.source_selection_anchor,
            context.source_selection_focus,
        )
        if key == self._source_key and self._source is not None:
            return self._source

        renderer = SourcePaneRenderer(
            context.text_lines,
            context.text_start,
            content_rows,
            line_width,
            context.current_path,
            context.wrap_text,
            context.text_x,
            context.text_search_query,
            context.text_search_current_line,
            context.text_search_current_column,
            preview_is_git_diff=context.preview_is_git_diff,
            source_selection_anchor=context.source_selection_anchor,
            source_selection_focus=context.source_selection_focus,
        )
        presentation = _SourcePresentation(
            rows=tuple(renderer.render_row(row) for row in range(content_rows)),
            text_percent=renderer.text_percent,
            status_start=renderer.status_start,
            status_end=renderer.status_end,
            status_total=renderer.status_total,
        )
        self._source_key = key
        self._source = presentation
        self._preview_renders += 1
        return presentation

    def _render_tree(
        self,
        context: RenderContext,
        *,
        content_rows: int,
        left_width: int,
        theme: UITheme,
    ) -> tuple[str, ...]:
        key = (
            left_width,
            content_rows,
            _container_identity(context.tree_entries),
            context.tree_start,
            context.tree_selected,
            context.tree_root,
            _container_identity(context.tree_roots),
            _container_identity(context.workspace_expanded),
            _container_identity(context.expanded),
            context.show_tree_sizes,
            _container_identity(context.git_status_overlay),
            context.tree_search_query,
            context.tree_filter_active,
            context.tree_filter_row_visible,
            context.tree_filter_query,
            context.tree_filter_editing,
            context.tree_filter_cursor_visible,
            context.tree_filter_match_count,
            context.tree_filter_truncated,
            context.tree_filter_loading,
            context.tree_filter_spinner_frame,
            context.tree_filter_prefix,
            context.tree_filter_placeholder,
            context.picker_active,
            context.picker_mode,
            context.picker_query,
            _container_identity(context.picker_items),
            context.picker_selected,
            context.picker_focus,
            context.picker_list_start,
            context.picker_message,
            theme,
        )
        if key == self._tree_key:
            return self._tree_rows

        renderer = TreePaneRenderer(
            left_width=left_width,
            content_rows=content_rows,
            tree_entries=context.tree_entries,
            tree_start=context.tree_start,
            tree_selected=context.tree_selected,
            tree_root=context.tree_root,
            tree_roots=context.tree_roots,
            workspace_expanded=context.workspace_expanded,
            expanded=context.expanded,
            show_tree_sizes=context.show_tree_sizes,
            git_status_overlay=context.git_status_overlay,
            tree_search_query=context.tree_search_query,
            tree_filter_active=context.tree_filter_active,
            tree_filter_row_visible=context.tree_filter_row_visible,
            tree_filter_query=context.tree_filter_query,
            tree_filter_editing=context.tree_filter_editing,
            tree_filter_cursor_visible=context.tree_filter_cursor_visible,
            tree_filter_match_count=context.tree_filter_match_count,
            tree_filter_truncated=context.tree_filter_truncated,
            tree_filter_loading=context.tree_filter_loading,
            tree_filter_spinner_frame=context.tree_filter_spinner_frame,
            tree_filter_prefix=context.tree_filter_prefix,
            tree_filter_placeholder=context.tree_filter_placeholder,
            picker_active=context.picker_active,
            picker_mode=context.picker_mode,
            picker_query=context.picker_query,
            picker_items=context.picker_items,
            picker_selected=context.picker_selected,
            picker_focus=context.picker_focus,
            picker_list_start=context.picker_list_start,
            picker_message=context.picker_message,
            theme=theme,
        )
        rows = tuple(renderer.padded_row_text(row) for row in range(content_rows))
        self._tree_key = key
        self._tree_rows = rows
        self._tree_renders += 1
        return rows

    def _render_text_only(
        self,
        context: RenderContext,
        theme: UITheme,
        help_presentation: _HelpPresentation,
        content_rows: int,
    ) -> Frame:
        width = max(1, context.width)
        source = self._render_source(context, content_rows=content_rows, line_width=width)
        help_rows = tuple(
            clip_ansi_line(_help_line(help_presentation.text_only_lines, row), width)
            for row in range(help_presentation.rows)
        )
        status = self._status_row(context, source, theme)

        text = ""
        if self._canonical_text:
            out = ["\033[H\033[J"]
            for row in source.rows:
                out.append(row)
                if "\033" in row:
                    out.append(theme.reset)
                out.append("\r\n")
            for row in help_rows:
                out.append(row)
                if "\033" in row:
                    out.append(theme.reset)
                out.append("\r\n")
            out.append(status)
            text = "".join(out)

        surfaces: list[Surface] = [
            Surface("preview", Rect(0, 0, width, content_rows), source.rows),
        ]
        if help_rows:
            surfaces.append(
                Surface(
                    "preview-help",
                    Rect(0, content_rows, width, len(help_rows)),
                    help_rows,
                )
            )
        surfaces.append(Surface("status", Rect(0, context.max_lines, width, 1), (status,)))
        return Frame(
            text,
            width=width,
            height=context.max_lines + 1,
            surfaces=tuple(surfaces),
        )

    def _render_split(
        self,
        context: RenderContext,
        theme: UITheme,
        help_presentation: _HelpPresentation,
        content_rows: int,
    ) -> Frame:
        width = max(1, context.width)
        left_width = clamp_left_width(width, context.left_width)
        right_width = max(1, width - left_width - 1)
        source = self._render_source(
            context,
            content_rows=content_rows,
            line_width=right_width,
        )
        tree_rows = self._render_tree(
            context,
            content_rows=content_rows,
            left_width=left_width,
            theme=theme,
        )
        left_help_rows = tuple(
            self._padded_left_help(
                _help_line(help_presentation.tree_lines, row),
                left_width,
            )
            for row in range(help_presentation.rows)
        )
        right_help_rows = tuple(
            clip_ansi_line(_help_line(help_presentation.text_lines, row), right_width)
            for row in range(help_presentation.rows)
        )
        divider = f"{theme.divider}│{theme.reset}" if theme.divider else "│"
        divider_rows = (divider,) * context.max_lines
        status = self._status_row(context, source, theme)

        text = ""
        if self._canonical_text:
            out = ["\033[H\033[J"]
            for row, tree_row in enumerate(tree_rows):
                out.extend((tree_row, divider, source.rows[row]))
                if "\033" in source.rows[row]:
                    out.append(theme.reset)
                out.append("\r\n")
            for row, left_help in enumerate(left_help_rows):
                right_help = right_help_rows[row]
                out.extend((left_help, divider, right_help))
                if "\033" in right_help:
                    out.append(theme.reset)
                out.append("\r\n")
            out.append(status)
            text = "".join(out)

        surfaces: list[Surface] = [
            Surface("tree", Rect(0, 0, left_width, content_rows), tree_rows),
            Surface(
                "divider",
                Rect(left_width, 0, 1, context.max_lines),
                divider_rows,
            ),
            Surface(
                "preview",
                Rect(left_width + 1, 0, right_width, content_rows),
                source.rows,
            ),
        ]
        if left_help_rows:
            surfaces.extend(
                (
                    Surface(
                        "tree-help",
                        Rect(0, content_rows, left_width, len(left_help_rows)),
                        left_help_rows,
                    ),
                    Surface(
                        "preview-help",
                        Rect(
                            left_width + 1,
                            content_rows,
                            right_width,
                            len(right_help_rows),
                        ),
                        right_help_rows,
                    ),
                )
            )
        surfaces.append(Surface("status", Rect(0, context.max_lines, width, 1), (status,)))
        return Frame(
            text,
            width=width,
            height=context.max_lines + 1,
            surfaces=tuple(surfaces),
        )

    @staticmethod
    def _padded_left_help(text: str, width: int) -> str:
        clipped = clip_ansi_line(text, width)
        plain = ANSI_ESCAPE_RE.sub("", clipped)
        visible = sum(char_display_width(ch, 0) for ch in plain)
        if visible < width:
            return clipped + (" " * (width - visible))
        return clipped

    @staticmethod
    def _status_row(
        context: RenderContext,
        source: _SourcePresentation,
        theme: UITheme,
    ) -> str:
        left_status = _compose_status_left_text(
            context.current_path,
            source.status_start,
            source.status_end,
            source.status_total,
            source.text_percent,
            context.status_message,
        )
        status = build_status_line(left_status, context.width)
        return f"{theme.reverse}{status}{theme.reset}"


def render_dual_page_context(context: RenderContext) -> Frame:
    """Render a standalone frame without retaining state between calls."""
    return RetainedPageRenderer().render(context)


def render_dual_page(
    text_lines: list[str],
    text_start: int,
    tree_entries: list[TreeEntry],
    tree_start: int,
    tree_selected: int,
    max_lines: int,
    current_path: Path,
    tree_root: Path,
    expanded: set[Path],
    width: int,
    left_width: int,
    text_x: int,
    wrap_text: bool,
    browser_visible: bool,
    show_hidden: bool,
    show_help: bool = False,
    show_tree_sizes: bool = True,
    status_message: str = "",
    tree_filter_active: bool = False,
    tree_filter_row_visible: bool = True,
    tree_filter_mode: str = "files",
    tree_filter_query: str = "",
    tree_filter_editing: bool = False,
    tree_filter_cursor_visible: bool = False,
    tree_filter_match_count: int = 0,
    tree_filter_truncated: bool = False,
    tree_filter_loading: bool = False,
    tree_filter_spinner_frame: int = 0,
    tree_filter_prefix: str = "p>",
    tree_filter_placeholder: str = "type to filter files",
    picker_active: bool = False,
    picker_mode: str = "symbols",
    picker_query: str = "",
    picker_items: list[str] | None = None,
    picker_selected: int = 0,
    picker_focus: str = "query",
    picker_list_start: int = 0,
    picker_message: str = "",
    git_status_overlay: dict[Path, int] | None = None,
    tree_search_query: str = "",
    text_search_query: str = "",
    text_search_current_line: int = 0,
    text_search_current_column: int = 0,
    preview_is_git_diff: bool = False,
    source_selection_anchor: tuple[int, int] | None = None,
    source_selection_focus: tuple[int, int] | None = None,
    tree_roots: list[Path] | None = None,
    workspace_expanded: list[set[Path]] | None = None,
    theme: UITheme | None = None,
) -> Frame:
    """Render one deterministic full frame for compatibility and capture tests."""
    return render_dual_page_context(
        RenderContext(
            text_lines=text_lines,
            text_start=text_start,
            tree_entries=tree_entries,
            tree_start=tree_start,
            tree_selected=tree_selected,
            max_lines=max_lines,
            current_path=current_path,
            tree_root=tree_root,
            expanded=expanded,
            width=width,
            left_width=left_width,
            text_x=text_x,
            wrap_text=wrap_text,
            browser_visible=browser_visible,
            show_hidden=show_hidden,
            tree_roots=tree_roots,
            workspace_expanded=workspace_expanded,
            show_help=show_help,
            show_tree_sizes=show_tree_sizes,
            status_message=status_message,
            tree_filter_active=tree_filter_active,
            tree_filter_row_visible=tree_filter_row_visible,
            tree_filter_mode=tree_filter_mode,
            tree_filter_query=tree_filter_query,
            tree_filter_editing=tree_filter_editing,
            tree_filter_cursor_visible=tree_filter_cursor_visible,
            tree_filter_match_count=tree_filter_match_count,
            tree_filter_truncated=tree_filter_truncated,
            tree_filter_loading=tree_filter_loading,
            tree_filter_spinner_frame=tree_filter_spinner_frame,
            tree_filter_prefix=tree_filter_prefix,
            tree_filter_placeholder=tree_filter_placeholder,
            picker_active=picker_active,
            picker_mode=picker_mode,
            picker_query=picker_query,
            picker_items=picker_items,
            picker_selected=picker_selected,
            picker_focus=picker_focus,
            picker_list_start=picker_list_start,
            picker_message=picker_message,
            git_status_overlay=git_status_overlay,
            tree_search_query=tree_search_query,
            text_search_query=text_search_query,
            text_search_current_line=text_search_current_line,
            text_search_current_column=text_search_current_column,
            preview_is_git_diff=preview_is_git_diff,
            source_selection_anchor=source_selection_anchor,
            source_selection_focus=source_selection_focus,
            theme=theme or DEFAULT_THEME,
        )
    )


__all__ = [
    "Frame",
    "Rect",
    "RenderContext",
    "RenderStats",
    "RetainedPageRenderer",
    "Surface",
    "build_status_line",
    "render_dual_page",
    "render_dual_page_context",
    "render_help_page",
]
