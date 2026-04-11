import json
import shutil
from pathlib import Path

from core.config import (
    WARN_SCORE,
    KICK_SCORE,
    BAN_SCORE,
    INVITE_SCORE,
    INVITE_ACCEPT_BONUS,
)

BASE_DIR = Path(__file__).resolve().parents[2] / "data"
DATA_FILE = BASE_DIR / "leaderboard.json"
TEMPLATE_FILE = BASE_DIR / "leaderboard.template.json"


def _ensure_data_file_exists():
    BASE_DIR.mkdir(parents=True, exist_ok=True)

    if not DATA_FILE.exists() and TEMPLATE_FILE.exists():
        shutil.copy(TEMPLATE_FILE, DATA_FILE)


leaderboard_data = {
    "staff": {},
    "monthly": {},

    # preserved extras
    "monthly_reset_key": None,
    "pending_invites_by_target": {},
}


def _blank_staff_entry(name: str) -> dict:
    return {
        "name": name,
        "warn": 0,
        "kick": 0,
        "ban": 0,
        "invite": 0,
        "invite_accept": 0,
        "points": 0,
    }


def _recalculate_points(entry: dict) -> int:
    return (
        int(entry.get("warn", 0)) * WARN_SCORE
        + int(entry.get("kick", 0)) * KICK_SCORE
        + int(entry.get("ban", 0)) * BAN_SCORE
        + int(entry.get("invite", 0)) * INVITE_SCORE
        + int(entry.get("invite_accept", 0)) * INVITE_ACCEPT_BONUS
    )


def _normalize_new_format(data: dict) -> dict:
    staff = data.get("staff", {})
    monthly = data.get("monthly", {})

    if not isinstance(staff, dict):
        staff = {}

    if not isinstance(monthly, dict):
        monthly = {}

    for section in (staff, monthly):
        for staff_id, entry in list(section.items()):
            if not isinstance(entry, dict):
                section[staff_id] = _blank_staff_entry(str(staff_id))
                continue

            entry.setdefault("name", str(staff_id))
            entry.setdefault("warn", 0)
            entry.setdefault("kick", 0)
            entry.setdefault("ban", 0)
            entry.setdefault("invite", 0)
            entry.setdefault("invite_accept", 0)

            entry["points"] = _recalculate_points(entry)

    return {
        "staff": staff,
        "monthly": monthly,

        # preserve other data
        "monthly_reset_key": data.get("monthly_reset_key"),
        "pending_invites_by_target": data.get("pending_invites_by_target", {}),
    }


def _migrate_legacy_format(data: dict) -> dict:
    staff = {}
    monthly = {}

    def ensure(section, name):
        if name not in section:
            section[name] = _blank_staff_entry(name)
        return section[name]

    for name, count in data.get("warnings", {}).items():
        ensure(staff, name)["warn"] = int(count)

    for name, count in data.get("kicks", {}).items():
        ensure(staff, name)["kick"] = int(count)

    for name, count in data.get("bans", {}).items():
        ensure(staff, name)["ban"] = int(count)

    for name, count in data.get("invites", {}).items():
        ensure(staff, name)["invite"] = int(count)

    for name, count in data.get("monthly_warnings", {}).items():
        ensure(monthly, name)["warn"] = int(count)

    for name, count in data.get("monthly_kicks", {}).items():
        ensure(monthly, name)["kick"] = int(count)

    for name, count in data.get("monthly_bans", {}).items():
        ensure(monthly, name)["ban"] = int(count)

    for name, count in data.get("monthly_invites", {}).items():
        ensure(monthly, name)["invite"] = int(count)

    for entry in staff.values():
        entry["points"] = _recalculate_points(entry)

    for entry in monthly.values():
        entry["points"] = _recalculate_points(entry)

    return {
        "staff": staff,
        "monthly": monthly,
        "monthly_reset_key": None,
        "pending_invites_by_target": {},
    }


def _normalize_leaderboard_data(data):
    if not isinstance(data, dict):
        return {
            "staff": {},
            "monthly": {},
            "monthly_reset_key": None,
            "pending_invites_by_target": {},
        }

    if "staff" in data or "monthly" in data:
        return _normalize_new_format(data)

    return _migrate_legacy_format(data)


def _replace_in_place(new_data):
    leaderboard_data.clear()

    leaderboard_data.update({
        "staff": dict(new_data.get("staff", {})),
        "monthly": dict(new_data.get("monthly", {})),
        "monthly_reset_key": new_data.get("monthly_reset_key"),
        "pending_invites_by_target": dict(
            new_data.get("pending_invites_by_target", {})
        ),
    })


def load_leaderboard_data():
    _ensure_data_file_exists()

    if not DATA_FILE.exists():
        _replace_in_place(leaderboard_data)
        return

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)

    normalized = _normalize_leaderboard_data(raw)
    _replace_in_place(normalized)


def save_leaderboard_data():
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(leaderboard_data, f, indent=2)


def reset_leaderboard_data():
    _replace_in_place({
        "staff": {},
        "monthly": {},
        "monthly_reset_key": None,
        "pending_invites_by_target": {},
    })

    save_leaderboard_data()


def reset_monthly_leaderboard_data():
    leaderboard_data["monthly"] = {}
    save_leaderboard_data()
