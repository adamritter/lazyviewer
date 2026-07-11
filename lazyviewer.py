#!/usr/bin/env python3
"""Compatibility script entrypoint for lazyviewer.

It delegates directly to ``lazyviewer.cli.main``.
Keep this file tiny so packaging and launcher behavior stays stable.
"""

import sys


if sys.version_info < (3, 10):
    raise SystemExit(
        "lazyviewer requires Python 3.10 or newer; "
        "use the project's .venv/bin/lazyviewer entry point."
    )


from lazyviewer.cli import main


if __name__ == "__main__":
    main()
