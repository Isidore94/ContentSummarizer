"""Persistent desktop-app settings and frozen-executable path helpers."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

APP_NAME = "ContentSummarizer"
DETAIL_LEVELS = ("simple", "detailed", "complex")


def executable_dir() -> Path:
    """Directory containing the executable, or this source checkout in dev."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def data_dir() -> Path:
    """Writable per-user directory for settings and logs."""
    override = os.environ.get("CONTENT_SUMMARIZER_DATA_DIR", "").strip()
    if override:
        root = Path(override).expanduser()
    else:
        root = Path(
            os.environ.get("LOCALAPPDATA")
            or os.environ.get("APPDATA")
            or Path.home()
        ) / APP_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def settings_path() -> Path:
    return data_dir() / "settings.json"


def default_summary_dir() -> Path:
    documents = Path.home() / "Documents"
    return documents / "ContentSummarizer Summaries"


def load_runtime_env() -> Path | None:
    """Load .env next to the exe/source without ever bundling credentials."""
    candidates = [executable_dir() / ".env", Path.cwd() / ".env"]
    for candidate in candidates:
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return candidate
    load_dotenv(override=False)
    return None


def normalize_detail(value: str | None) -> str:
    detail = (value or "simple").strip().lower()
    return detail if detail in DETAIL_LEVELS else "simple"


def load_settings() -> dict[str, object]:
    defaults: dict[str, object] = {
        "summary_folder": str(default_summary_dir()),
        "summary_detail": "simple",
        "auto_start": True,
    }
    path = settings_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return defaults
    if not isinstance(raw, dict):
        return defaults
    defaults.update(raw)
    defaults["summary_folder"] = str(
        Path(str(defaults["summary_folder"])).expanduser()
    )
    defaults["summary_detail"] = normalize_detail(
        str(defaults["summary_detail"])
    )
    defaults["auto_start"] = bool(defaults["auto_start"])
    return defaults


def save_settings(settings: dict[str, object]) -> Path:
    path = settings_path()
    clean = {
        "summary_folder": str(Path(str(settings["summary_folder"])).expanduser()),
        "summary_detail": normalize_detail(str(settings["summary_detail"])),
        "auto_start": bool(settings.get("auto_start", True)),
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(clean, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path
