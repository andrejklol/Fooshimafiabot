import json
import shutil
from pathlib import Path
from typing import Any

from core.cache import app_state

BASE_DIR = Path(__file__).resolve().parents[2] / "data"
DATA_FILE = BASE_DIR / "repeat_offenders.json"
TEMPLATE_FILE = BASE_DIR / "repeat_offenders.template.json"

# Global in-memory dictionary
repeat_offenders: dict[str, Any] = {}


def _mark_dirty() -> None:
    try:
        app_state.leaderboard_dirty = True
    except Exception:
        pass


def _ensure_data_file_exists() -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    if DATA_FILE.exists():
        return

    if TEMPLATE_FILE.exists():
        try:
            shutil.copy(TEMPLATE_FILE, DATA_FILE)
            return
        except OSError:
            pass
    DATA_FILE.write_text("{}", encoding="utf-8")


def load_repeat_offenders() -> None:
    global repeat_offenders
    _ensure_data_file_exists()

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        repeat_offenders = data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError, ValueError):
        # Create an emergency backup file if data corruption is caught
        try:
            if DATA_FILE.exists():
                shutil.copy(DATA_FILE, DATA_FILE.with_suffix(".broken.json"))
        except OSError:
            pass
        repeat_offenders = {}
        save_repeat_offenders()


def save_repeat_offenders() -> None:
    """Flushes offenders to disk and safely drops the framework dirty flag."""
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(repeat_offenders, f, indent=2, ensure_ascii=False)
        try:
            app_state.leaderboard_dirty = False
        except Exception:
            pass
    except OSError as e:
        print(f"[offenders] critical failed to flush JSON to storage disk: {e}")


def reset_repeat_offenders() -> None:
    global repeat_offenders
    repeat_offenders.clear()
    save_repeat_offenders()


def ensure_structure() -> None:
    """Repairs keys, types, and enforces safety boundaries across raw JSON states."""
    changed = False
    for user_id, data in list(repeat_offenders.items()):
        if not isinstance(data, dict):
            repeat_offenders[user_id] = {
                "name": "Unknown",
                "warn": 0,
                "kick": 0,
                "ban": 0,
            }
            changed = True
            continue

        for key in ("warn", "kick", "ban"):
            if key not in data or data[key] is None:
                data[key] = 0
                changed = True
            else:
                try:
                    data[key] = int(data[key])
                except Exception:
                    data[key] = 0
                    changed = True

        if "name" not in data or not str(data["name"]).strip():
            data["name"] = f"User {str(user_id)[:8]}"
            changed = True

    if changed:
        save_repeat_offenders()
