"""Persistent JSON config helpers.

Stores pane-width presets, hidden-file preference, and named marks.
All access is defensive: malformed or missing config falls back safely.
"""

from __future__ import annotations

import json
from pathlib import Path

from platformdirs import user_config_dir

from .navigation import JumpLocation, is_named_mark_key

APP_NAME = "lazyviewer"
CONFIG_FILENAME = "config.json"
DEFAULT_CONFIG_PATH = Path(user_config_dir(APP_NAME, appauthor=False)) / CONFIG_FILENAME
LEGACY_CONFIG_PATH = Path.home() / ".config" / "lazyviewer.json"
CONFIG_PATH = DEFAULT_CONFIG_PATH


def _load_config_path() -> Path:
    """Return preferred config path, falling back to legacy location when needed."""
    if CONFIG_PATH.exists():
        return CONFIG_PATH
    if CONFIG_PATH == DEFAULT_CONFIG_PATH and LEGACY_CONFIG_PATH.exists():
        return LEGACY_CONFIG_PATH
    return CONFIG_PATH


def load_config() -> dict[str, object]:
    """Load the persisted JSON config object.

    Returns an empty dict when the file is missing, unreadable, malformed, or
    does not decode to a top-level JSON object.
    """
    config_path = _load_config_path()
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_config(data: dict[str, object]) -> None:
    """Persist config data as pretty-printed JSON.

    Any filesystem/serialization error is ignored to keep runtime behavior
    non-fatal when config cannot be written.
    """
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass


def _load_percent(key: str) -> float | None:
    """Read a percentage config value constrained to the open interval (0, 100)."""
    data = load_config()
    value = data.get(key)
    if not isinstance(value, (int, float)):
        return None
    if value <= 0 or value >= 100:
        return None
    return float(value)


def _save_percent(key: str, total_width: int, left_width: int) -> None:
    """Store a pane width as a bounded percentage.

    ``left_width / total_width`` is clamped to ``[1.0, 99.0]`` and rounded to
    two decimals before persisting.
    """
    if total_width <= 0:
        return
    percent = max(1.0, min(99.0, (left_width / total_width) * 100.0))
    config = load_config()
    config[key] = round(percent, 2)
    save_config(config)


def load_left_pane_percent() -> float | None:
    """Load the default split-pane left width percentage."""
    return _load_percent("left_pane_percent")


def save_left_pane_percent(total_width: int, left_width: int) -> None:
    """Persist the default split-pane left width percentage."""
    _save_percent("left_pane_percent", total_width, left_width)


def load_content_search_left_pane_percent() -> float | None:
    """Load the left-pane percentage used while content-search mode is active."""
    return _load_percent("content_search_left_pane_percent")


def save_content_search_left_pane_percent(total_width: int, left_width: int) -> None:
    """Persist content-search-specific split-pane width."""
    _save_percent("content_search_left_pane_percent", total_width, left_width)


def load_show_hidden() -> bool:
    """Return persisted hidden-file visibility preference.

    Only explicit boolean values are accepted; any other type falls back to
    ``False``.
    """
    value = load_config().get("show_hidden")
    return bool(value) if isinstance(value, bool) else False


def save_show_hidden(show_hidden: bool) -> None:
    """Persist hidden-file visibility preference as a boolean."""
    config = load_config()
    config["show_hidden"] = bool(show_hidden)
    save_config(config)


def load_theme_name() -> str | None:
    """Load persisted UI theme name, returning ``None`` when unset/invalid."""
    value = load_config().get("theme")
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped if stripped else None


def save_theme_name(theme_name: str) -> None:
    """Persist selected UI theme name."""
    stripped = str(theme_name).strip()
    if not stripped:
        return
    config = load_config()
    config["theme"] = stripped
    save_config(config)


def _coerce_nonnegative_int(value: object) -> int:
    """Normalize JSON scalar values for scroll offsets.

    Booleans and non-integers are treated as invalid and coerced to ``0``.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def load_named_marks() -> dict[str, JumpLocation]:
    """Load named jump marks from config with strict validation.

    Invalid mark keys, malformed entries, and non-string paths are dropped.
    ``start`` and ``text_x`` are coerced to non-negative integers.
    """
    value = load_config().get("named_marks")
    if not isinstance(value, dict):
        return {}

    marks: dict[str, JumpLocation] = {}
    for key, raw_location in value.items():
        if not isinstance(key, str) or not is_named_mark_key(key):
            continue
        if not isinstance(raw_location, dict):
            continue

        raw_path = raw_location.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            continue

        marks[key] = JumpLocation(
            path=Path(raw_path),
            start=_coerce_nonnegative_int(raw_location.get("start", 0)),
            text_x=_coerce_nonnegative_int(raw_location.get("text_x", 0)),
        )
    return marks


def save_named_marks(named_marks: dict[str, JumpLocation]) -> None:
    """Persist named jump marks in normalized JSON form.

    Only valid mark keys mapped to ``JumpLocation`` instances are written.
    Paths are serialized as strings and offsets are clamped non-negative.
    """
    serialized: dict[str, dict[str, object]] = {}
    for key, location in named_marks.items():
        if not is_named_mark_key(key) or not isinstance(location, JumpLocation):
            continue
        normalized = location.normalized()
        serialized[key] = {
            "path": str(normalized.path),
            "start": max(0, normalized.start),
            "text_x": max(0, normalized.text_x),
        }

    config = load_config()
    config["named_marks"] = serialized
    save_config(config)
