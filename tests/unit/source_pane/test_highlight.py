"""Targeted tests for text sanitization and fallback highlighting.

Ensures control bytes are escaped while standard whitespace is preserved.
Also protects extensionless/plaintext spacing in fallback colorization paths.
"""

import re
import unittest
from pathlib import Path
from unittest import mock

from lazyviewer.source_pane.diff import _ADDED_BG_SGR, _apply_line_background
from lazyviewer.source_pane.highlighting import rendered_preview_row
from lazyviewer.source_pane.syntax import colorize_source, sanitize_terminal_text

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


class HighlightSanitizationTests(unittest.TestCase):
    def test_sanitize_terminal_text_escapes_control_bytes_but_keeps_common_whitespace(self) -> None:
        source = "a\tb\nc\rd\x07e\x1bf"
        sanitized = sanitize_terminal_text(source)

        self.assertEqual(sanitized, "a\tb\nc\rd\\x07e\\x1bf")
        self.assertNotIn("\x07", sanitized)
        self.assertNotIn("\x1b", sanitized)

    def test_fallback_colorize_keeps_spacing_for_extensionless_text(self) -> None:
        source = "Permission  is  hereby granted, free of charge:\n"

        with mock.patch("lazyviewer.source_pane.syntax.pygments_highlight", return_value=None):
            rendered = colorize_source(source, Path("LICENSE"))

        plain = ANSI_RE.sub("", rendered)
        self.assertEqual(plain, source)

    def test_git_diff_long_string_keeps_consistent_token_coloring_at_viewport_edge(self) -> None:
        ansi_line = (
            'prefix \033[33m"git is required for git diff color integration tests"'
            "\033[39;49;00m"
        )
        diff_line = _apply_line_background(ansi_line, _ADDED_BG_SGR)
        rendered = rendered_preview_row(
            [diff_line],
            0,
            width=20,
            wrap_text=False,
            text_x=7,
            text_search_query="",
            text_search_current_line=0,
            text_search_current_column=0,
            has_current_text_hit=False,
            selection_range=None,
            preview_is_git_diff=True,
        )
        self.assertEqual(ANSI_RE.sub("", rendered), '"git is required for')

        foreground = "default"
        nonspace_foregrounds: list[str] = []
        idx = 0
        while idx < len(rendered):
            if rendered[idx] == "\x1b":
                match = ANSI_RE.match(rendered, idx)
                if match and match.group(0).endswith("m"):
                    params = match.group(0)[2:-1]
                    parts = [part for part in params.split(";") if part]
                    if not parts:
                        parts = ["0"]
                    part_idx = 0
                    while part_idx < len(parts):
                        part = parts[part_idx]
                        try:
                            token = int(part)
                        except ValueError:
                            part_idx += 1
                            continue
                        if token == 0:
                            foreground = "default"
                        elif token == 39:
                            foreground = "default"
                        elif 30 <= token <= 37 or 90 <= token <= 97:
                            foreground = str(token)
                        elif token in {38, 48} and part_idx + 1 < len(parts):
                            mode = parts[part_idx + 1]
                            if mode == "5" and part_idx + 2 < len(parts):
                                if token == 38:
                                    foreground = f"38;5;{parts[part_idx + 2]}"
                                part_idx += 2
                            elif mode == "2" and part_idx + 4 < len(parts):
                                if token == 38:
                                    foreground = ";".join(["38", "2", *parts[part_idx + 2 : part_idx + 5]])
                                part_idx += 4
                        part_idx += 1
                    idx = match.end()
                    continue
            char = rendered[idx]
            if char not in "\r\n" and not char.isspace():
                nonspace_foregrounds.append(foreground)
            idx += 1

        self.assertGreaterEqual(len(nonspace_foregrounds), 2)
        self.assertEqual(nonspace_foregrounds[-2], "33")
        self.assertEqual(nonspace_foregrounds[-1], "33")


if __name__ == "__main__":
    unittest.main()
