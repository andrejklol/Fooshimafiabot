from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from core.config import (
    BAN_SCORE,
    INVITE_ACCEPT_BONUS,
    INVITE_SCORE,
    KICK_SCORE,
    WARN_SCORE,
)

BASE_DIR: Path = Path(__file__).resolve().parents[2] / "data"
DATA_FILE: Path = BASE_DIR / "leaderboard.json"
TEMPLATE_FILE: Path = BASE_DIR / "leaderboard.template.json"

RANK_PRIORITY: dict[str, int] = {
    "Owner": 0,
    "Underboss": 1,
    "Consigliere": 2,
    "Capo": 3,
    "Soldier": 4,
    "Unknown Rank": 5,
}

# In-memory working database reference
leaderboard_data: dict[str, Any] = {
    "staff": {},
    "monthly": {},
    "archive": {},
    "monthly_reset_key": None,
    "pending_invites_by_target": {},
}


def _ensure_data_file_exists() -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    if not DATA_FILE.exists() and TEMPLATE_FILE.exists():
        shutil.copy(TEMPLATE_FILE, DATA_FILE)


def _blank_staff_entry(name: str, staff_id: str | None = None) -> dict[str, Any]:
    uid = str(staff_id or name)
    return {
        "id": uid,
        "name": name,
        "rank_name": "Unknown Rank",
        "warn": 0,
        "kick": 0,
        "ban": 0,
        "invite": 0,
        "invite_accept": 0,
        "points": 0,
    }


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_rank_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "Unknown Rank"

    lowered = text.casefold()
    if lowered in {"owner", "godfooshi", "god"}:
        return "Owner"
    if lowered == "underboss":
        return "Underboss"
    if lowered == "consigliere":
        return "Consigliere"
    if lowered == "capo":
        return "Capo"
    if lowered in {"soldier", "staff", "moderator", "mod"}:
        return "Soldier"

    return text


def _clean_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "null":
        return None
    return text


def _recalculate_points(entry: dict[str, Any]) -> int:
    return (
        _safe_int(entry.get("warn")) * WARN_SCORE
        + _safe_int(entry.get("kick")) * KICK_SCORE
        + _safe_int(entry.get("ban")) * BAN_SCORE
        + _safe_int(entry.get("invite")) * INVITE_SCORE
        + _safe_int(entry.get("invite_accept")) * INVITE_ACCEPT_BONUS
    )


def _normalize_staff_section(section_data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(section_data, dict):
        return {}

    normalized: dict[str, dict[str, Any]] = {}

    for staff_id, entry in section_data.items():
        sid = str(staff_id)
        if not isinstance(entry, dict):
            normalized[sid] = _blank_staff_entry(sid, sid)
            continue

        fixed = dict(entry)
        fixed["id"] = str(fixed.get("id") or sid)
        fixed["name"] = str(fixed.get("name") or sid)
        fixed["rank_name"] = _normalize_rank_name(fixed.get("rank_name"))

        for field in ("warn", "kick", "ban", "invite", "invite_accept"):
            fixed[field] = _safe_int(fixed.get(field))

        fixed["points"] = _recalculate_points(fixed)

        # Map non-metric metadata fields cleanly
        meta_fields = ("discord_id", "discord_username", "discord_avatar_url", "vrchat_avatar_url", "vrchat_username")
        for key in meta_fields:
            cleaned = _clean_optional_string(fixed.get(key))
            if cleaned is not None:
                fixed[key] = cleaned
            else:
                fixed.pop(key, None)

        normalized[sid] = fixed

    return normalized


def _normalize_leaderboard_data(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {
            "staff": {},
            "monthly": {},
            "archive": {},
            "monthly_reset_key": None,
            "pending_invites_by_target": {},
        }

    if any(k in data for k in ("staff", "monthly", "archive")):
        staff = _normalize_staff_section(data.get("staff", {}))
        monthly = _normalize_staff_section(data.get("monthly", {}))
        archive = _normalize_staff_section(data.get("archive", {}))

        for entry in archive.values():
            entry.setdefault("archived_at", None)
            entry.setdefault("archive_reason", None)

        return {
            "staff": staff,
            "monthly": monthly,
            "archive": archive,
            "monthly_reset_key": data.get("monthly_reset_key"),
            "pending_invites_by_target": dict(data.get("pending_invites_by_target") or {}),
        }

    # Legacy translation engine fallback
    staff, monthly = {}, {}

    def ensure(sec: dict[str, Any], name: str) -> dict[str, Any]:
        return sec.setdefault(str(name), _blank_staff_entry(str(name), str(name)))

    legacy_metrics = (
        ("warnings", "warn"),
        ("kicks", "kick"),
        ("bans", "ban"),
        ("invites", "invite"),
        ("invite_accepts", "invite_accept"),
    )
    for src_key, target_field in legacy_metrics:
        for name, count in data.get(src_key, {}).items():
            ensure(staff, name)[target_field] = _safe_int(count)
        for name, count in data.get(f"monthly_{src_key}", {}).items():
            ensure(monthly, name)[target_field] = _safe_int(count)

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


def _replace_in_place(new_data: dict[str, Any]) -> None:
    leaderboard_data.clear()
    leaderboard_data.update({
        "staff": dict(new_data.get("staff", {})),
        "monthly": dict(new_data.get("monthly", {})),
        "archive": dict(new_data.get("archive", {})),
        "monthly_reset_key": new_data.get("monthly_reset_key"),
        "pending_invites_by_target": dict(new_data.get("pending_invites_by_target", {})),
    })


def _build_sorted_output(data: dict[str, Any]) -> dict[str, Any]:
    def rank_sort_key(item: tuple[str, Any]) -> tuple[int, int, str]:
        sid, entry = item
        if not isinstance(entry, dict):
            return (999, 0, str(sid).casefold())
        rank = _normalize_rank_name(entry.get("rank_name"))
        return (RANK_PRIORITY.get(rank, 999), -_safe_int(entry.get("points")), str(entry.get("name") or sid).strip().casefold())

    def archive_sort_key(item: tuple[str, Any]) -> tuple[str, str]:
        sid, entry = item
        if not isinstance(entry, dict):
            return ("9999-99", str(sid).casefold())
        return (str(entry.get("archived_at") or ""), str(entry.get("name") or sid).strip().casefold())

    return {
        "staff": dict(sorted(data.get("staff", {}).items(), key=rank_sort_key)),
        "monthly": dict(sorted(data.get("monthly", {}).items(), key=rank_sort_key)),
        "monthly_reset_key": data.get("monthly_reset_key"),
        "pending_invites_by_target": dict(data.get("pending_invites_by_target", {})),
        "archive": dict(sorted(data.get("archive", {}).items(), key=archive_sort_key)),
    }


def load_leaderboard_data() -> None:
    _ensure_data_file_exists()
    if not DATA_FILE.exists():
        _replace_in_place(leaderboard_data)
        return

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)

    _replace_in_place(_build_sorted_output(_normalize_leaderboard_data(raw)))


def save_leaderboard_data() -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    sorted_output = _build_sorted_output(_normalize_leaderboard_data(leaderboard_data))

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted_output, f, indent=2, ensure_ascii=False)

    _replace_in_place(sorted_output)


def reset_leaderboard_data() -> None:
    _replace_in_place({
        "staff": {},
        "monthly": {},
        "archive": {},
        "monthly_reset_key": None,
        "pending_invites_by_target": {},
    })
    save_leaderboard_data()


def reset_monthly_leaderboard_data() -> None:
    leaderboard_data["monthly"] = {}
    save_leaderboard_data()
