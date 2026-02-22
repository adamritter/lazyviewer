from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from lazyviewer import config


class ConfigBehaviorTests(unittest.TestCase):
    def test_content_search_left_pane_percent_uses_distinct_config_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "lazyviewer.json"
            with mock.patch("lazyviewer.config.CONFIG_PATH", config_path):
                config.save_left_pane_percent(100, 32)
                config.save_content_search_left_pane_percent(100, 61)

                saved = config.load_config()
                self.assertEqual(saved.get("left_pane_percent"), 32.0)
                self.assertEqual(saved.get("content_search_left_pane_percent"), 61.0)
                self.assertEqual(config.load_left_pane_percent(), 32.0)
                self.assertEqual(config.load_content_search_left_pane_percent(), 61.0)


if __name__ == "__main__":
    unittest.main()
