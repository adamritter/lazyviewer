"""Command-line front door for lazyviewer.

Parses CLI options, resolves the target path, and loads source text.
Then dispatches into the interactive pager runtime.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .app_runtime import run_pager
from .highlight import read_text


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
    parser.add_argument("--no-color", action="store_true", help="Disable color output even on TTY.")
    parser.add_argument("--nopager", action="store_true", help="Print output directly without interactive paging.")
    args = parser.parse_args()

    if default_path is None:
        default_path = Path.cwd()
    path = Path(args.path or default_path)
    if not path.exists():
        raise SystemExit(f"Path not found: {path}")

    source = "" if path.is_dir() else read_text(path)
    run_pager(source, path, args.style, args.no_color, args.nopager)


if __name__ == "__main__":
    main()
