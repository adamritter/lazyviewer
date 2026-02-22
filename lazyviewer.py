#!/usr/bin/env python3
"""Compatibility script entrypoint for lazyviewer.

It delegates directly to ``lazyviewer.cli.main``.
Keep this file tiny so packaging and launcher behavior stays stable.
"""

from lazyviewer.cli import main


if __name__ == "__main__":
    main()
