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
    "archive": {},

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


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _recalculate_points(entry: dict) -> int:
    return (
        _safe_int(entry.get("warn", 0)) * WARN_SCORE
        + _safe_int(entry.get("kick", 0)) * KICK_SCORE
        + _safe_int(entry.get("ban", 0)) * BAN_SCORE
        + _safe_int(entry.get("invite", 0)) * INVITE_SCORE
        + _safe_int(entry.get("invite_accept", 0)) * INVITE_ACCEPT_BONUS
    )


def _normalize_staff_section(section_data: dict) -> dict:
    if not isinstance(section_data, dict):
        section_data = {}

    normalized = {}

    for staff_id, entry in section_data.items():
        if not isinstance(entry, dict):
            normalized[str(staff_id)] = _blank_staff_entry(str(staff_id))
            continue

        fixed = dict(entry)
        fixed.setdefault("id", str(staff_id))
        fixed.setdefault("name", str(staff_id))
        fixed.setdefault("warn", 0)
        fixed.setdefault("kick", 0)
        fixed.setdefault("ban", 0)
        fixed.setdefault("invite", 0)
        fixed.setdefault("invite_accept", 0)

        fixed["warn"] = _safe_int(fixed.get("warn", 0))
        fixed["kick"] = _safe_int(fixed.get("kick", 0))
        fixed["ban"] = _safe_int(fixed.get("ban", 0))
        fixed["invite"] = _safe_int(fixed.get("invite", 0))
        fixed["invite_accept"] = _safe_int(fixed.get("invite_accept", 0))
        fixed["points"] = _recalculate_points(fixed)

        normalized[str(staff_id)] = fixed

    return normalized


def _normalize_new_format(data: dict) -> dict:
    staff = _normalize_staff_section(data.get("staff", {}))
    monthly = _normalize_staff_section(data.get("monthly", {}))
    archive = _normalize_staff_section(data.get("archive", {}))

    for entry in archive.values():
        entry.setdefault("archived_at", None)

    return {
        "staff": staff,
        "monthly": monthly,
        "archive": archive,

        # preserve other data
        "monthly_reset_key": data.get("monthly_reset_key"),
        "pending_invites_by_target": (
            data.get("pending_invites_by_target", {})
            if isinstance(data.get("pending_invites_by_target", {}), dict)
            else {}
        ),
    }


def _migrate_legacy_format(data: dict) -> dict:
    staff = {}
    monthly = {}

    def ensure(section, name):
        if name not in section:
            section[name] = _blank_staff_entry(name)
        return section[name]

    for name, count in data.get("warnings", {}).items():
        ensure(staff, name)["warn"] = _safe_int(count)

    for name, count in data.get("kicks", {}).items():
        ensure(staff, name)["kick"] = _safe_int(count)

    for name, count in data.get("bans", {}).items():
        ensure(staff, name)["ban"] = _safe_int(count)

    for name, count in data.get("invites", {}).items():
        ensure(staff, name)["invite"] = _safe_int(count)

    for name, count in data.get("monthly_warnings", {}).items():
        ensure(monthly, name)["warn"] = _safe_int(count)

    for name, count in data.get("monthly_kicks", {}).items():
        ensure(monthly, name)["kick"] = _safe_int(count)

    for name, count in data.get("monthly_bans", {}).items():
        ensure(monthly, name)["ban"] = _safe_int(count)

    for name, count in data.get("monthly_invites", {}).items():
        ensure(monthly, name)["invite"] = _safe_int(count)

    for entry in staff.values():
        entry["points"] = _recalculate_points(entry)

    for entry in monthly.values():
        entry["points"] = _recalculate_points(entry)

    return {
        "staff": staff,
        "monthly": monthly,
        "archive": {},
        "monthly_reset_key": None,
        "pending_invites_by_target": {},
    }


def _normalize_leaderboard_data(data):
    if not isinstance(data, dict):
        return {
            "staff": {},
            "monthly": {},
            "archive": {},
            "monthly_reset_key": None,
            "pending_invites_by_target": {},
        }

    if "staff" in data or "monthly" in data or "archive" in data:
        return _normalize_new_format(data)

    return _migrate_legacy_format(data)


def _replace_in_place(new_data):
    leaderboard_data.clear()

    leaderboard_data.update({
        "staff": dict(new_data.get("staff", {})),
        "monthly": dict(new_data.get("monthly", {})),
        "archive": dict(new_data.get("archive", {})),
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
        "archive": {},
        "monthly_reset_key": None,
        "pending_invites_by_target": {},
    })

    save_leaderboard_data()


def reset_monthly_leaderboard_data():
    leaderboard_data["monthly"] = {}
    save_leaderboard_data()
