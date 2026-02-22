"""Targeted tests for terminal-text sanitization.

Ensures control bytes are escaped while standard whitespace is preserved.
Prevents preview rendering from emitting unsafe terminal effects.
"""

import unittest

from lazyviewer.highlight import sanitize_terminal_text


class HighlightSanitizationTests(unittest.TestCase):
    def test_sanitize_terminal_text_escapes_control_bytes_but_keeps_common_whitespace(self) -> None:
        source = "a\tb\nc\rd\x07e\x1bf"
        sanitized = sanitize_terminal_text(source)

        self.assertEqual(sanitized, "a\tb\nc\rd\\x07e\\x1bf")
        self.assertNotIn("\x07", sanitized)
        self.assertNotIn("\x1b", sanitized)


if __name__ == "__main__":
    unittest.main()
