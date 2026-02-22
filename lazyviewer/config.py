from __future__ import annotations

import json
from pathlib import Path

from .navigation import JumpLocation, is_named_mark_key

CONFIG_PATH = Path.home() / ".config" / "lazyviewer.json"


def load_config() -> dict[str, object]:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_config(data: dict[str, object]) -> None:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass


def _load_percent(key: str) -> float | None:
    data = load_config()
    value = data.get(key)
    if not isinstance(value, (int, float)):
        return None
    if value <= 0 or value >= 100:
        return None
    return float(value)


def _save_percent(key: str, total_width: int, left_width: int) -> None:
    if total_width <= 0:
        return
    percent = max(1.0, min(99.0, (left_width / total_width) * 100.0))
    config = load_config()
    config[key] = round(percent, 2)
    save_config(config)


def load_left_pane_percent() -> float | None:
    return _load_percent("left_pane_percent")


def save_left_pane_percent(total_width: int, left_width: int) -> None:
    _save_percent("left_pane_percent", total_width, left_width)


def load_content_search_left_pane_percent() -> float | None:
    return _load_percent("content_search_left_pane_percent")


def save_content_search_left_pane_percent(total_width: int, left_width: int) -> None:
    _save_percent("content_search_left_pane_percent", total_width, left_width)


def load_show_hidden() -> bool:
    value = load_config().get("show_hidden")
    return bool(value) if isinstance(value, bool) else False


def save_show_hidden(show_hidden: bool) -> None:
    config = load_config()
    config["show_hidden"] = bool(show_hidden)
    save_config(config)


def _coerce_nonnegative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def load_named_marks() -> dict[str, JumpLocation]:
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
