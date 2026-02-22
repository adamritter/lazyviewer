from __future__ import annotations

import json
from pathlib import Path

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


def load_left_pane_percent() -> float | None:
    data = load_config()
    value = data.get("left_pane_percent")
    if not isinstance(value, (int, float)):
        return None
    if value <= 0 or value >= 100:
        return None
    return float(value)


def save_left_pane_percent(total_width: int, left_width: int) -> None:
    if total_width <= 0:
        return
    percent = max(1.0, min(99.0, (left_width / total_width) * 100.0))
    config = load_config()
    config["left_pane_percent"] = round(percent, 2)
    save_config(config)


def load_show_hidden() -> bool:
    value = load_config().get("show_hidden")
    return bool(value) if isinstance(value, bool) else False


def save_show_hidden(show_hidden: bool) -> None:
    config = load_config()
    config["show_hidden"] = bool(show_hidden)
    save_config(config)
