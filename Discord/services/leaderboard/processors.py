from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from core.cache import app_state
from core.config import (
    BAN_SCORE,
    INVITE_ACCEPT_BONUS,
    INVITE_SCORE,
    KICK_SCORE,
    WARN_SCORE,
)

from .storage import leaderboard_data, save_leaderboard_data

DEFAULT_ENTRY: dict[str, Any] = {
    "warn": 0,
    "kick": 0,
    "ban": 0,
    "invite": 0,
    "invite_accept": 0,
    "points": 0,
    "rank_name": "Unknown Rank",
}

ARCHIVE_STATUS_SECTION = "archive_status"


# -------------------------
# INTERNAL HELPERS
# -------------------------

def _mark_leaderboard_dirty() -> None:
    try:
        app_state.leaderboard_dirty = True
    except Exception:
        pass


def _save_if_needed(save: bool) -> None:
    if save:
        save_leaderboard_data()
        try:
            app_state.leaderboard_dirty = False
        except Exception:
            pass
    else:
        _mark_leaderboard_dirty()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _isoformat(dt: datetime | None) -> str | None:
    normalized = _to_utc(dt)
    return normalized.isoformat() if normalized else None


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return _to_utc(value)

    text = str(value).strip()
    if not text:
        return None

    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return _to_utc(datetime.fromisoformat(text))
    except Exception:
        return None


def _safe_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value

    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y", "on"}:
        return True
    if text in {"false", "0", "no", "n", "off"}:
        return False
    return None


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


# -------------------------
# NAME HANDLING
# -------------------------

def _is_placeholder_name(name: str | None) -> bool:
    if not name:
        return True
    text = str(name).strip()
    return not text or text.startswith("User ")


def _best_cached_name(user_id: str) -> str | None:
    cached = getattr(app_state, "target_name_cache", {}).get(str(user_id))
    return str(cached).strip() if cached and not _is_placeholder_name(cached) else None


def resolve_username(user_id: str, username: str | None) -> str:
    cleaned = (username or "").strip()

    if cleaned and not _is_placeholder_name(cleaned):
        try:
            cache = getattr(app_state, "target_name_cache", None)
            if not isinstance(cache, dict):
                app_state.target_name_cache = {}
                cache = app_state.target_name_cache
            cache[str(user_id)] = cleaned
        except Exception:
            pass
        return cleaned

    for section in ("staff", "monthly", "archive"):
        existing_name = leaderboard_data.get(section, {}).get(user_id, {}).get("name")
        if existing_name and not _is_placeholder_name(existing_name):
            return existing_name

    cached_name = _best_cached_name(user_id)
    if cached_name:
        return cached_name

    return f"User {user_id[:8]}"


def _normalize_rank_name(rank_name: str | None) -> str:
    text = str(rank_name or "").strip()
    return text if text else "Unknown Rank"


def _ensure_sections() -> None:
    for section in ("staff", "monthly", "archive", "pending_invites_by_target", ARCHIVE_STATUS_SECTION):
        leaderboard_data.setdefault(section, {})
    leaderboard_data.setdefault("monthly_reset_key", None)


def _base_entry(user_id: str, username: str | None, rank_name: str | None = None) -> dict[str, Any]:
    return {
        "id": user_id,
        "name": resolve_username(user_id, username),
        **{k: v for k, v in DEFAULT_ENTRY.items() if k != "rank_name"},
        "rank_name": _normalize_rank_name(rank_name),
    }


def _sync_name_across_sections(user_id: str, resolved_name: str) -> None:
    for section_name in ("staff", "monthly", "archive"):
        section = leaderboard_data.get(section_name, {})
        if user_id in section and isinstance(section[user_id], dict):
            existing_name = section[user_id].get("name")
            if _is_placeholder_name(resolved_name) and existing_name and not _is_placeholder_name(existing_name):
                continue
            section[user_id]["name"] = resolved_name


def _sync_rank_across_sections(user_id: str, rank_name: str) -> None:
    for section_name in ("staff", "monthly", "archive"):
        section = leaderboard_data.get(section_name, {})
        if user_id in section and isinstance(section[user_id], dict):
            section[user_id]["rank_name"] = rank_name


# -------------------------
# ENTRY MANAGEMENT
# -------------------------

def ensure_staff_entry(user_id: str, username: str | None, rank_name: str | None = None) -> dict[str, Any]:
    _ensure_sections()
    user_id = str(user_id)
    resolved_name = resolve_username(user_id, username)
    normalized_rank = _normalize_rank_name(rank_name)

    for target_key in ("staff", "monthly"):
        if user_id not in leaderboard_data[target_key] or not isinstance(leaderboard_data[target_key].get(user_id), dict):
            fallback_rank = normalized_rank
            if target_key == "monthly" and "staff" in leaderboard_data and user_id in leaderboard_data["staff"]:
                fallback_rank = leaderboard_data["staff"][user_id].get("rank_name", normalized_rank)
            leaderboard_data[target_key][user_id] = _base_entry(user_id, resolved_name, fallback_rank)
        else:
            entry = leaderboard_data[target_key][user_id]
            entry["id"] = user_id
            for key, default_value in DEFAULT_ENTRY.items():
                entry.setdefault(key, default_value)

            existing_name = entry.get("name")
            if not (_is_placeholder_name(resolved_name) and existing_name and not _is_placeholder_name(existing_name)):
                entry["name"] = resolved_name

            entry["rank_name"] = normalized_rank if rank_name else _normalize_rank_name(entry.get("rank_name"))

    _sync_name_across_sections(user_id, resolved_name)
    if rank_name:
        _sync_rank_across_sections(user_id, normalized_rank)

    _mark_leaderboard_dirty()
    return leaderboard_data["staff"][user_id]


def set_rank_name(user_id: str, rank_name: str) -> None:
    _ensure_sections()
    user_id = str(user_id)
    normalized_rank = _normalize_rank_name(rank_name)
    _sync_rank_across_sections(user_id, normalized_rank)
    _mark_leaderboard_dirty()


def update_staff_name(user_id: str, username: str | None) -> str:
    _ensure_sections()
    user_id = str(user_id)
    resolved_name = resolve_username(user_id, username)
    _sync_name_across_sections(user_id, resolved_name)
    _mark_leaderboard_dirty()
    return resolved_name


# -------------------------
# ACTION PROCESSING
# -------------------------

def award_action(
    user_id: str,
    username: str | None,
    action: str,
    points_to_add: int,
    rank_name: str | None = None,
    save: bool = True,
) -> dict[str, Any]:
    _ensure_sections()
    user_id = str(user_id)

    if action not in {"warn", "kick", "ban", "invite", "invite_accept"}:
        raise ValueError(f"Unsupported leaderboard action: {action}")

    ensure_staff_entry(user_id, username, rank_name)
    resolved_name = resolve_username(user_id, username)

    for entry_type in ("staff", "monthly"):
        entry = leaderboard_data[entry_type][user_id]
        if not (_is_placeholder_name(resolved_name) and not _is_placeholder_name(entry.get("name"))):
            entry["name"] = resolved_name
        if rank_name:
            entry["rank_name"] = _normalize_rank_name(rank_name)

        entry[action] = _int(entry.get(action, 0)) + 1
        entry["points"] = _int(entry.get("points", 0)) + _int(points_to_add)

    _save_if_needed(save)
    return leaderboard_data["staff"][user_id]


def add_warn(user_id, username, points_to_add=WARN_SCORE, rank_name=None, save=True):
    return award_action(user_id, username, "warn", points_to_add, rank_name, save=save)


def add_kick(user_id, username, points_to_add=KICK_SCORE, rank_name=None, save=True):
    return award_action(user_id, username, "kick", points_to_add, rank_name, save=save)


def add_ban(user_id, username, points_to_add=BAN_SCORE, rank_name=None, save=True):
    return award_action(user_id, username, "ban", points_to_add, rank_name, save=save)


def add_invite(user_id, username, points_to_add=INVITE_SCORE, rank_name=None, save=True):
    return award_action(user_id, username, "invite", points_to_add, rank_name, save=save)


def add_invite_accept(user_id, username, points_to_add=INVITE_ACCEPT_BONUS, rank_name=None, save=True):
    return award_action(user_id, username, "invite_accept", points_to_add, rank_name, save=save)


# -------------------------
# INVITE TRACKING
# -------------------------

def record_pending_invite(inviter_id: str, target_id: str, save: bool = True) -> None:
    _ensure_sections()
    leaderboard_data["pending_invites_by_target"][str(target_id)] = str(inviter_id)
    _save_if_needed(save)


def resolve_invite_accept(target_id: str, save: bool = True) -> str | None:
    _ensure_sections()
    target_id = str(target_id)

    pending = leaderboard_data.get("pending_invites_by_target", {})
    inviter_id = pending.pop(target_id, None)
    if not inviter_id:
        return None

    inviter_id = str(inviter_id)
    inviter_entry = leaderboard_data.get("staff", {}).get(inviter_id, {})
    
    add_invite_accept(
        inviter_id,
        inviter_entry.get("name"),
        rank_name=inviter_entry.get("rank_name"),
        save=False,
    )

    _save_if_needed(save)
    return inviter_id


def get_pending_invite_for_target(target_id: str) -> str | None:
    _ensure_sections()
    return leaderboard_data.get("pending_invites_by_target", {}).get(str(target_id))


def cancel_pending_invite(target_id: str, save: bool = True) -> bool:
    _ensure_sections()
    pending = leaderboard_data.get("pending_invites_by_target", {})
    existed = str(target_id) in pending
    pending.pop(str(target_id), None)
    if existed:
        _save_if_needed(save)
    return existed


# -------------------------
# AUDIT LOG SUPPORT
# -------------------------

def _get_action_type(action: str | None) -> str | None:
    if not action:
        return None

    text = str(action).lower().strip()
    mapping = {
        "warn": "warn", "warning": "warn",
        "kick": "kick", "ban": "ban",
        "invite": "invite", "invite_accept": "invite_accept",
        "invite accepted": "invite_accept",
    }
    if text in mapping:
        return mapping[text]

    if "invite_accept" in text or "invite accepted" in text:
        return "invite_accept"
    if "warn" in text or "warning" in text:
        return "warn"
    if "kick" in text:
        return "kick"
    if "ban" in text:
        return "ban"
    if "invite" in text:
        return "invite"
    return None


def _extract_attr(entry, attributes: list[str]) -> str | None:
    for attr in attributes:
        val = getattr(entry, attr, None)
        if val:
            return str(val).strip()
    return None


def _extract_actor_id(entry) -> str | None:
    return _extract_attr(entry, ["actor_id", "user_id", "id"])


def _extract_actor_name(entry) -> str | None:
    return _extract_attr(entry, ["actor_name", "actor_display_name", "display_name", "username", "name"])


def _extract_rank_name(entry) -> str | None:
    return _extract_attr(entry, ["rank_name"])


def _extract_action(entry) -> str | None:
    candidates = ["action", "event_type", "eventType", "description"]
    for cand in candidates:
        matched = _get_action_type(getattr(entry, cand, None))
        if matched:
            return matched
    return None


def _extract_target_id(entry) -> str | None:
    return _extract_attr(entry, ["target_id", "target_user_id", "invited_id", "invited_user_id"])


async def process_audit_log_entry(entry, bot=None, save: bool = True) -> tuple[bool, bool]:
    user_id = _extract_actor_id(entry)
    username = _extract_actor_name(entry)
    rank_name = _extract_rank_name(entry)
    action = _extract_action(entry)

    if not user_id or not action:
        return False, False

    user_id = str(user_id)
    
    if action == "warn":
        add_warn(user_id, username, rank_name=rank_name, save=False)
    elif action == "kick":
        add_kick(user_id, username, rank_name=rank_name, save=False)
    elif action == "ban":
        add_ban(user_id, username, rank_name=rank_name, save=False)
    elif action == "invite":
        add_invite(user_id, username, rank_name=rank_name, save=False)
        target_id = _extract_target_id(entry)
        if target_id:
            record_pending_invite(inviter_id=user_id, target_id=target_id, save=False)
    elif action == "invite_accept":
        add_invite_accept(user_id, username, rank_name=rank_name, save=False)
    else:
        return False, False

    _save_if_needed(save)
    return True, False


# -------------------------
# ARCHIVE STATE HELPERS
# -------------------------

def _get_archive_status(user_id: str) -> dict[str, Any] | None:
    _ensure_sections()
    status = leaderboard_data.get(ARCHIVE_STATUS_SECTION, {}).get(str(user_id))
    return status if isinstance(status, dict) else None


def _set_archive_status(user_id: str, payload: dict[str, Any]) -> None:
    _ensure_sections()
    leaderboard_data[ARCHIVE_STATUS_SECTION][str(user_id)] = payload
    _mark_leaderboard_dirty()


def _clear_archive_status(user_id: str) -> None:
    _ensure_sections()
    leaderboard_data.get(ARCHIVE_STATUS_SECTION, {}).pop(str(user_id), None)
    _mark_leaderboard_dirty()


# -------------------------
# ARCHIVE SUPPORT
# -------------------------

def archive_staff(user_id, username=None, archive_reason="No longer staff", archived_at=None):
    _ensure_sections()
    user_id = str(user_id)

    staff_entry = leaderboard_data.get("staff", {}).get(user_id)
    monthly_entry = leaderboard_data.get("monthly", {}).get(user_id)
    archive_entry = leaderboard_data.get("archive", {}).get(user_id)

    if isinstance(staff_entry, dict):
        base = dict(staff_entry)
    elif isinstance(archive_entry, dict):
        base = dict(archive_entry)
    else:
        base = _base_entry(user_id, username)

    resolved_name = resolve_username(
        user_id,
        username or base.get("name") or (monthly_entry or {}).get("name"),
    )

    base["id"] = user_id
    base["name"] = resolved_name
    base["rank_name"] = _normalize_rank_name(base.get("rank_name"))

    if isinstance(monthly_entry, dict):
        base["monthly_snapshot"] = {
            k: _int(monthly_entry.get(k, 0))
            for k in ("warn", "kick", "ban", "invite", "invite_accept", "points")
        }

    base["archive_reason"] = archive_reason
    base["archived_at"] = archived_at if archived_at else _isoformat(_utcnow())

    leaderboard_data["archive"][user_id] = base
    leaderboard_data.get("staff", {}).pop(user_id, None)
    leaderboard_data.get("monthly", {}).pop(user_id, None)

    _clear_archive_status(user_id)
    _save_if_needed(True)


def unarchive_staff(user_id, username=None, rank_name=None):
    _ensure_sections()
    user_id = str(user_id)

    archived_entry = leaderboard_data.get("archive", {}).pop(user_id, None)
    fallback_name = username
    fallback_rank = rank_name
    monthly_snapshot = None

    if isinstance(archived_entry, dict):
        fallback_name = fallback_name or archived_entry.get("name")
        fallback_rank = fallback_rank or archived_entry.get("rank_name")
        monthly_snapshot = archived_entry.get("monthly_snapshot")

    entry = ensure_staff_entry(user_id, fallback_name, fallback_rank)

    if isinstance(archived_entry, dict):
        for key in ("warn", "kick", "ban", "invite", "invite_accept", "points"):
            entry[key] = _int(archived_entry.get(key, entry.get(key, 0)))

    monthly_entry = leaderboard_data["monthly"].get(user_id)
    if isinstance(monthly_entry, dict) and isinstance(monthly_snapshot, dict):
        for key in ("warn", "kick", "ban", "invite", "invite_accept", "points"):
            monthly_entry[key] = _int(monthly_snapshot.get(key, monthly_entry.get(key, 0)))

    _clear_archive_status(user_id)
    _save_if_needed(True)
    return entry


def sync_staff_archive_state(
    user_id: str,
    username: str | None = None,
    rank_name: str | None = None,
    *,
    has_vrc_staff_role: bool | None = None,
    has_discord_staff_role: bool | None = None,
    now: datetime | None = None,
    archive_after_hours: int = 24,
) -> dict[str, Any]:
    _ensure_sections()

    user_id = str(user_id)
    now_dt = _to_utc(now) or _utcnow()

    has_vrc = _safe_bool(has_vrc_staff_role)
    has_discord = _safe_bool(has_discord_staff_role)

    archive_status = _get_archive_status(user_id) or {}
    is_archived = isinstance(leaderboard_data.get("archive", {}).get(user_id), dict)
    both_role_states_known = has_vrc is not None and has_discord is not None

    if has_vrc is True or has_discord is True:
        if is_archived:
            restored = unarchive_staff(user_id, username=username, rank_name=rank_name)
            return {
                "status": "unarchived", "user_id": user_id, "name": restored.get("name"),
                "has_vrc_staff_role": has_vrc, "has_discord_staff_role": has_discord,
            }

        ensure_staff_entry(user_id, username, rank_name)
        _clear_archive_status(user_id)
        _save_if_needed(True)
        return {
            "status": "active", "user_id": user_id,
            "has_vrc_staff_role": has_vrc, "has_discord_staff_role": has_discord,
        }

    if not both_role_states_known:
        ensure_staff_entry(user_id, username, rank_name)
        _save_if_needed(True)
        return {
            "status": "active_unknown_roles", "user_id": user_id,
            "has_vrc_staff_role": has_vrc, "has_discord_staff_role": has_discord,
        }

    pending_since = _parse_datetime(archive_status.get("pending_since"))
    if pending_since is None:
        pending_since = now_dt
        _set_archive_status(user_id, {
            "pending_since": _isoformat(pending_since),
            "has_vrc_staff_role": has_vrc, "has_discord_staff_role": has_discord,
        })
        ensure_staff_entry(user_id, username, rank_name)
        _save_if_needed(True)
        return {
            "status": "pending_archive", "user_id": user_id, "pending_since": _isoformat(pending_since),
            "hours_remaining": archive_after_hours, "has_vrc_staff_role": has_vrc, "has_discord_staff_role": has_discord,
        }

    elapsed = now_dt - pending_since
    threshold = timedelta(hours=archive_after_hours)

    if elapsed >= threshold:
        archive_staff(
            user_id, username=username,
            archive_reason="Both VRChat and Discord staff roles missing",
            archived_at=_isoformat(now_dt),
        )
        return {
            "status": "archived", "user_id": user_id, "archived_at": _isoformat(now_dt),
            "has_vrc_staff_role": has_vrc, "has_discord_staff_role": has_discord,
        }

    remaining = threshold - elapsed
    hours_remaining = max(0, int(remaining.total_seconds() // 3600))

    ensure_staff_entry(user_id, username, rank_name)
    _save_if_needed(True)

    return {
        "status": "pending_archive", "user_id": user_id, "pending_since": _isoformat(pending_since),
        "hours_remaining": hours_remaining, "has_vrc_staff_role": has_vrc, "has_discord_staff_role": has_discord,
    }


# -------------------------
# REPAIR TOOL
# -------------------------

def repair_all_names():
    _ensure_sections()
    all_ids = set(leaderboard_data.get("staff", {}).keys()) | \
              set(leaderboard_data.get("monthly", {}).keys()) | \
              set(leaderboard_data.get("archive", {}).keys())

    for user_id in all_ids:
        candidate_name = None
        for section_name in ("staff", "monthly", "archive"):
            name = leaderboard_data.get(section_name, {}).get(user_id, {}).get("name")
            if name and not _is_placeholder_name(name):
                candidate_name = name
                break

        if not candidate_name:
            candidate_name = _best_cached_name(user_id)

        resolved = resolve_username(user_id, candidate_name)
        _sync_name_across_sections(user_id, resolved)

    _save_if_needed(True)


# -------------------------
# VRC SYNC
# -------------------------

async def sync_all_vrc_staff_into_leaderboard(bot=None, force_refresh=False) -> int:
    _ensure_sections()
    
    vrc_staff = None
    if bot is not None:
        vrc_staff = getattr(bot, "vrc_staff", None)
        if vrc_staff is None:
            b_state = getattr(bot, "app_state", None)
            vrc_staff = getattr(b_state, "vrc_staff", None) or getattr(b_state, "group_staff", None) or getattr(b_state, "staff_cache", None)
        if vrc_staff is None:
            v_client = getattr(bot, "vrchat_client", None)
            vrc_staff = getattr(v_client, "vrc_staff", None) or getattr(v_client, "group_staff", None) or getattr(v_client, "staff_cache", None)

    if vrc_staff is None:
        vrc_staff = getattr(app_state, "vrc_staff", None) or getattr(app_state, "group_staff", None) or getattr(app_state, "staff_cache", None) or []

    if not isinstance(vrc_staff, list):
        vrc_staff = []

    synced = 0
    for member in vrc_staff:
        is_dict = isinstance(member, dict)
        
        user_id = member.get("id") or member.get("user_id") or member.get("usr_id") or member.get("userId") if is_dict else \
                  getattr(member, "id", None) or getattr(member, "user_id", None) or getattr(member, "usr_id", None) or getattr(member, "userId", None)

        username = member.get("name") or member.get("username") or member.get("display_name") or member.get("displayName") if is_dict else \
                   getattr(member, "name", None) or getattr(member, "username", None) or getattr(member, "display_name", None) or getattr(member, "displayName", None)

        rank_name = member.get("rank_name") or member.get("rank") if is_dict else \
                    getattr(member, "rank_name", None) or getattr(member, "rank", None)

        has_vrc = member.get("has_vrc_staff_role", True) if is_dict else getattr(member, "has_vrc_staff_role", True)
        has_disc = member.get("has_discord_staff_role") if is_dict else getattr(member, "has_discord_staff_role", None)

        if not user_id:
            continue

        result = sync_staff_archive_state(
            str(user_id), username=username, rank_name=rank_name,
            has_vrc_staff_role=has_vrc, has_discord_staff_role=has_disc,
        )

        if result.get("status") in {"active", "active_unknown_roles", "unarchived", "pending_archive"}:
            synced += 1

    _save_if_needed(True)
    return synced
