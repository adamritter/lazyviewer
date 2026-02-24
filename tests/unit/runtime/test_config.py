"""Tests for config persistence and input sanitization.

Validates pane-width keys and named-mark round-tripping.
Ensures malformed config data is safely normalized on load.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lazyviewer.runtime import config
from lazyviewer.runtime.navigation import JumpLocation


class ConfigBehaviorTests(unittest.TestCase):
    def test_content_search_left_pane_percent_uses_distinct_config_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lazyviewer.json"
            with mock.patch("lazyviewer.runtime.config.CONFIG_PATH", config_path):
                config.save_left_pane_percent(100, 32)
                config.save_content_search_left_pane_percent(100, 61)

                saved = config.load_config()
                self.assertEqual(saved.get("left_pane_percent"), 32.0)
                self.assertEqual(saved.get("content_search_left_pane_percent"), 61.0)
                self.assertEqual(config.load_left_pane_percent(), 32.0)
                self.assertEqual(config.load_content_search_left_pane_percent(), 61.0)

    def test_named_marks_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lazyviewer.json"
            mark_a_path = (Path(tmp) / "a.py").resolve()
            mark_b_path = (Path(tmp) / "b.py").resolve()
            expected = {
                "a": JumpLocation(path=mark_a_path, start=12, text_x=4),
                "'": JumpLocation(path=mark_b_path, start=0, text_x=9),
            }
            with mock.patch("lazyviewer.runtime.config.CONFIG_PATH", config_path):
                config.save_named_marks(expected)
                self.assertEqual(config.load_named_marks(), expected)

    def test_load_named_marks_sanitizes_invalid_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lazyviewer.json"
            with mock.patch("lazyviewer.runtime.config.CONFIG_PATH", config_path):
                config.save_config(
                    {
                        "named_marks": {
                            "a": {"path": "/tmp/a.py", "start": -5, "text_x": 3},
                            "ok": {"path": "/tmp/skip.py", "start": 1, "text_x": 1},
                            " ": {"path": "/tmp/skip2.py", "start": 2, "text_x": 2},
                            "b": {"path": "/tmp/b.py", "start": True, "text_x": False},
                            "c": {"path": "/tmp/c.py", "start": 7.5, "text_x": 2},
                            "d": {"path": 42, "start": 1, "text_x": 1},
                            "e": "bad-shape",
                        }
                    }
                )

                loaded = config.load_named_marks()

            self.assertEqual(set(loaded.keys()), {"a", "b", "c"})
            self.assertEqual(loaded["a"], JumpLocation(path=Path("/tmp/a.py"), start=0, text_x=3))
            self.assertEqual(loaded["b"], JumpLocation(path=Path("/tmp/b.py"), start=0, text_x=0))
            self.assertEqual(loaded["c"], JumpLocation(path=Path("/tmp/c.py"), start=0, text_x=2))


if __name__ == "__main__":
    unittest.main()
