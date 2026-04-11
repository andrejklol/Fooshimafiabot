import json
import shutil
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2] / "data"

DATA_FILE = BASE_DIR / "repeat_offenders.json"
TEMPLATE_FILE = BASE_DIR / "repeat_offenders.template.json"


def _ensure_data_file_exists():
    BASE_DIR.mkdir(parents=True, exist_ok=True)

    if not DATA_FILE.exists() and TEMPLATE_FILE.exists():
        shutil.copy(TEMPLATE_FILE, DATA_FILE)


repeat_offenders = {}


def load_repeat_offenders():
    global repeat_offenders

    _ensure_data_file_exists()

    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            repeat_offenders = json.load(f)
    else:
        repeat_offenders = {}


def save_repeat_offenders():
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(repeat_offenders, f, indent=2)


def reset_repeat_offenders():
    global repeat_offenders

    repeat_offenders = {}

    save_repeat_offenders()
