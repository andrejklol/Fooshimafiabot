import json
import shutil
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2] / "data"

DATA_FILE = BASE_DIR / "repeat_offenders.json"
TEMPLATE_FILE = BASE_DIR / "repeat_offenders.template.json"


def _ensure_data_file_exists():
    BASE_DIR.mkdir(parents=True, exist_ok=True)

    if DATA_FILE.exists():
        return

    # copy template if available
    if TEMPLATE_FILE.exists():
        shutil.copy(TEMPLATE_FILE, DATA_FILE)
    else:
        DATA_FILE.write_text("{}", encoding="utf-8")


repeat_offenders: dict = {}


def load_repeat_offenders():
    global repeat_offenders

    _ensure_data_file_exists()

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            repeat_offenders = json.load(f)

        # safety check
        if not isinstance(repeat_offenders, dict):
            repeat_offenders = {}

    except (json.JSONDecodeError, OSError, ValueError):

        # backup broken file
        broken_file = DATA_FILE.with_suffix(".broken.json")

        try:
            if DATA_FILE.exists():
                shutil.copy(DATA_FILE, broken_file)
        except OSError:
            pass

        repeat_offenders = {}
        save_repeat_offenders()


def save_repeat_offenders():
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(
            repeat_offenders,
            f,
            indent=2,
            ensure_ascii=False
        )


def reset_repeat_offenders():
    global repeat_offenders

    repeat_offenders = {}
    save_repeat_offenders()


# ensures keys exist if old json structure is missing fields
def ensure_structure():
    changed = False

    for user_id, data in repeat_offenders.items():

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
            if key not in data:
                data[key] = 0
                changed = True

        if "name" not in data:
            data["name"] = "Unknown"
            changed = True

    if changed:
        save_repeat_offenders()
