"""Desktop, model, and Application Support paths."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_DIR_NAME = "MeetingMinutes"


def data_dir() -> Path:
    override = os.environ.get("MEETING_MINUTES_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / APP_DIR_NAME


def sessions_dir() -> Path:
    path = data_dir() / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def desktop_dir() -> Path:
    override = os.environ.get("MEETING_MINUTES_DESKTOP")
    if override:
        path = Path(override).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path
    path = Path.home() / "Desktop"
    path.mkdir(parents=True, exist_ok=True)
    return path


def models_dir() -> Path:
    override = os.environ.get("MEETING_MINUTES_MODELS")
    if override:
        return Path(override).expanduser()
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "models"
    return Path(__file__).resolve().parents[2] / "models"


def resource_path(*parts: str) -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")).joinpath(*parts)
    return Path(__file__).resolve().parents[2].joinpath(*parts)


def load_user_settings() -> dict:
    defaults = {"threshold": 0.55, "favorites": []}
    path = data_dir() / "settings.json"
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                defaults.update(loaded)
        except (OSError, json.JSONDecodeError):
            pass
    try:
        threshold = float(defaults.get("threshold", 0.55))
    except (TypeError, ValueError):
        threshold = 0.55
    defaults["threshold"] = min(0.90, max(0.30, threshold))
    favorites = defaults.get("favorites") or []
    if not isinstance(favorites, list):
        favorites = []
    defaults["favorites"] = [str(item).strip() for item in favorites if str(item).strip()][:20]
    return defaults


def save_user_settings(settings: dict) -> None:
    path = data_dir() / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
