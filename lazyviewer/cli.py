"""Command-line front door for lazyviewer.

Parses CLI options, resolves the target path, and loads source text.
Then dispatches into the interactive pager runtime.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from .render.ansi import build_screen_lines
from .runtime import run_pager
from .source_pane import SourcePane
from .source_pane.highlighting import rendered_preview_row
from .source_pane.syntax import read_text
from .ui_theme import available_theme_names


def _positive_int(value: str) -> int:
    """argparse type for positive integer values."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid integer value: {value!r}") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be >= 1")
    return parsed


def _default_render_width() -> int:
    """Resolve default source-render width from current terminal size."""
    term = shutil.get_terminal_size((80, 24))
    return max(1, term.columns)


def render_source_view(path: Path, style: str, no_color: bool, max_cols: int) -> str:
    """Render source-pane rows for ``path`` using UI rendering code paths."""
    target = path.resolve()
    rendered_for_path = SourcePane.build_rendered_for_path(
        target,
        show_hidden=False,
        style=style,
        no_color=no_color,
        dir_skip_gitignored=True,
        prefer_git_diff=True,
        dir_show_size_labels=True,
    )
    text_lines = build_screen_lines(rendered_for_path.text, max_cols, wrap=False)
    out: list[str] = []
    for text_idx in range(len(text_lines)):
        row = rendered_preview_row(
            text_lines,
            text_idx,
            max_cols,
            wrap_text=False,
            text_x=0,
            text_search_query="",
            text_search_current_line=0,
            text_search_current_column=0,
            has_current_text_hit=False,
            selection_range=None,
            preview_is_git_diff=rendered_for_path.is_git_diff_preview,
        )
        out.append(row)
        if "\033" in row:
            out.append("\033[0m")
        out.append("\n")
    return "".join(out)


def main(default_path: Path | None = None) -> None:
    """Parse CLI arguments and launch lazyviewer on a file or directory.

    ``default_path`` is primarily for tests; when omitted the current working
    directory is used. Directories launch with an empty source buffer while
    files are loaded through ``read_text`` first.
    """
    parser = argparse.ArgumentParser(
        description="Print file contents in a terminal pager with syntax highlighting."
    )
    parser.add_argument("path", nargs="?", default=None, help="Path to file. Defaults to current directory.")
    parser.add_argument("--style", default="monokai", help="Pygments style name (for pygments rendering).")
    parser.add_argument(
        "--theme",
        default=None,
        help=f"UI theme name ({', '.join(available_theme_names())}).",
    )
    parser.add_argument("--no-color", action="store_true", help="Disable color output even on TTY.")
    parser.add_argument("--nopager", action="store_true", help="Print output directly without interactive paging.")
    parser.add_argument("--render", metavar="PATH", help="Render source pane for PATH and exit.")
    parser.add_argument(
        "--max-cols",
        type=_positive_int,
        default=None,
        help="Column width for --render output (default: terminal width).",
    )
    args = parser.parse_args()

    if args.render is not None:
        if args.path is not None:
            raise SystemExit("Cannot combine positional path with --render.")
        render_path = Path(args.render)
        if not render_path.exists():
            raise SystemExit(f"Path not found: {render_path}")
        max_cols = args.max_cols if args.max_cols is not None else _default_render_width()
        sys.stdout.write(render_source_view(render_path, args.style, args.no_color, max_cols))
        return

    if default_path is None:
        default_path = Path.cwd()
    path = Path(args.path or default_path)
    if not path.exists():
        raise SystemExit(f"Path not found: {path}")

    source = "" if path.is_dir() else read_text(path)
    run_pager(source, path, args.style, args.no_color, args.nopager, args.theme)


if __name__ == "__main__":
    main()
