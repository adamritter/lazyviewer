from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .app import run_pager
from .highlight import read_text


def main(default_path: Path | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Print file contents in a terminal pager with syntax highlighting."
    )
    parser.add_argument("path", nargs="?", default=None, help="Path to file. Defaults to this script.")
    parser.add_argument("--style", default="monokai", help="Pygments style name (for pygments rendering).")
    parser.add_argument("--no-color", action="store_true", help="Disable color output even on TTY.")
    parser.add_argument("--nopager", action="store_true", help="Print output directly without interactive paging.")
    args = parser.parse_args()

    if default_path is None:
        default_path = Path(sys.argv[0]).resolve()
    path = Path(args.path or default_path)
    if not path.exists():
        raise SystemExit(f"Path not found: {path}")

    source = "" if path.is_dir() else read_text(path)
    run_pager(source, path, args.style, args.no_color, args.nopager)


if __name__ == "__main__":
    main()
