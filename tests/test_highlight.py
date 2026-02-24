"""Targeted tests for text sanitization and fallback highlighting.

Ensures control bytes are escaped while standard whitespace is preserved.
Also protects extensionless/plaintext spacing in fallback colorization paths.
"""

import re
import unittest
from pathlib import Path
from unittest import mock

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


if __name__ == "__main__":
    unittest.main()
