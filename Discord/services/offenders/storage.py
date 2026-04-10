import json
from pathlib import Path

DATA_FILE = Path("data/repeat_offenders.json")

repeat_offenders = {}


def load_repeat_offenders():

    global repeat_offenders

    if DATA_FILE.exists():

        with open(DATA_FILE, "r") as f:

            repeat_offenders = json.load(f)


def save_repeat_offenders():

    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(DATA_FILE, "w") as f:

        json.dump(repeat_offenders, f, indent=2)


def reset_repeat_offenders():

    global repeat_offenders

    repeat_offenders = {}

    save_repeat_offenders()