import logging
from datetime import datetime, timezone

from core.cache import app_state
from services.vrchat_client import (
    get_all_vrc_staff_members,
    vrc_user_is_staff,
)

from services.offenders.tracking import (
    add_warn,
    add_kick,
    add_ban,
)

from .scoring import get_action_score
from .storage import leaderboard_data, save_leaderboard_data


log = logging.getLogger("leaderboard_processors")

_MOD_ACTIONS = {"warn", "kick", "ban"}
_SUPPORTED_ACTIONS = {"warn", "kick", "ban", "invite", "invite_accept"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_section(section: str) -> None:
    if section not in leaderboard_data or not isinstance(leaderboard_data[section], dict):
        leaderboard_data[section] = {}


def _ensure_archive_section() -> None:
    _ensure_section("archive")


def _coerce_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _fallback_name_for_staff(staff_id: str) -> str:
    return f"User {staff_id.replace('usr_', '')[:8]}"


def _is_placeholder_name(name: str | None, staff_id: str | None = None) -> bool:
    text = str(name or "").strip()

    if not text:
        return True

    if text.lower() == "unknown":
        return True

    if staff_id:
        fallback = _fallback_name_for_staff(staff_id)
        if text == fallback:
            return True

    return False


def _pick_best_staff_name(
    staff_id: str,
    incoming_name: str | None,
    existing_name: str | None = None,
) -> str:
    incoming = str(incoming_name or "").strip()
    existing = str(existing_name or "").strip()
    fallback = _fallback_name_for_staff(staff_id)

    if incoming and not _is_placeholder_name(incoming, staff_id):
        return incoming

    if existing and not _is_placeholder_name(existing, staff_id):
        return existing

    if existing:
        return existing

    return fallback


def _build_default_staff_entry(staff_id: str, staff_name: str) -> dict:
    safe_name = _pick_best_staff_name(staff_id, staff_name)
    return {
        "id": staff_id,
        "name": safe_name,
        "warn": 0,
        "kick": 0,
        "ban": 0,
        "invite": 0,
        "invite_accept": 0,
        "points": 0,
    }


def _normalize_staff_entry(existing: dict, staff_id: str, staff_name: str) -> bool:
    changed = False
    default_entry = _build_default_staff_entry(staff_id, staff_name)

    for key, value in default_entry.items():
        if key not in existing:
            existing[key] = value
            changed = True

    for stat_key in ("warn", "kick", "ban", "invite", "invite_accept", "points"):
        normalized = _coerce_int(existing.get(stat_key, 0))
        if existing.get(stat_key) != normalized:
            existing[stat_key] = normalized
            changed = True

    if existing.get("id") != staff_id:
        existing["id"] = staff_id
        changed = True

    best_name = _pick_best_staff_name(
        staff_id,
        incoming_name=staff_name,
        existing_name=existing.get("name"),
    )

    if existing.get("name") != best_name:
        existing["name"] = best_name
        changed = True

    return changed


def _ensure_staff(section: str, staff_id: str, staff_name: str) -> bool:
    _ensure_section(section)

    default_entry = _build_default_staff_entry(staff_id, staff_name)
    existing = leaderboard_data[section].get(staff_id)

    if not isinstance(existing, dict):
        leaderboard_data[section][staff_id] = default_entry
        return True

    return _normalize_staff_entry(existing, staff_id, staff_name)


def _restore_archived_staff_if_present(staff_id: str, staff_name: str) -> bool:
    _ensure_section("staff")
    _ensure_archive_section()

    archived_entry = leaderboard_data["archive"].get(staff_id)
    if not isinstance(archived_entry, dict):
        return False

    restored_entry = dict(archived_entry)
    restored_entry.pop("archived_at", None)
    restored_entry["restored_at"] = _utc_now_iso()

    _normalize_staff_entry(restored_entry, staff_id, staff_name)
    leaderboard_data["staff"][staff_id] = restored_entry
    del leaderboard_data["archive"][staff_id]

    log.info("Restored archived staff member: %s (%s)", staff_name, staff_id)
    return True


def _archive_removed_staff(current_staff_ids: set[str]) -> bool:
    _ensure_section("staff")
    _ensure_archive_section()

    changed = False
    to_archive = []

    for staff_id in list(leaderboard_data["staff"].keys()):
        if staff_id not in current_staff_ids:
            to_archive.append(staff_id)

    for staff_id in to_archive:
        entry = leaderboard_data["staff"].pop(staff_id, None)
        if not isinstance(entry, dict):
            continue

        archived_entry = dict(entry)
        archived_entry["archived_at"] = _utc_now_iso()
        leaderboard_data["archive"][staff_id] = archived_entry
        changed = True

        log.warning(
            "Archived staff member due to missing from VRC sync: %s (%s)",
            archived_entry.get("name", "Unknown"),
            staff_id,
        )

    return changed


def _ensure_staff_in_needed_sections(
    staff_id: str,
    staff_name: str,
    monthly_only: bool = False,
) -> bool:
    changed = False

    if monthly_only:
        changed = _ensure_staff("monthly", staff_id, staff_name) or changed
    else:
        restored = _restore_archived_staff_if_present(staff_id, staff_name)
        changed = restored or changed

        changed = _ensure_staff("staff", staff_id, staff_name) or changed
        changed = _ensure_staff("monthly", staff_id, staff_name) or changed

    return changed


def _apply_action_to_section(
    section: str,
    staff_id: str,
    staff_name: str,
    action: str,
) -> None:
    _ensure_staff(section, staff_id, staff_name)

    if action not in _SUPPORTED_ACTIONS:
        return

    staff_entry = leaderboard_data[section][staff_id]

    if action == "invite":
        staff_entry["invite"] = _coerce_int(staff_entry.get("invite", 0)) + 1
        return

    if action == "invite_accept":
        staff_entry["invite_accept"] = _coerce_int(staff_entry.get("invite_accept", 0)) + 1
        staff_entry["invite"] = _coerce_int(staff_entry.get("invite_accept", 0))
        staff_entry["points"] = (
            _coerce_int(staff_entry.get("points", 0))
            + get_action_score("invite_accept")
        )
        return

    staff_entry[action] = _coerce_int(staff_entry.get(action, 0)) + 1
    staff_entry["points"] = (
        _coerce_int(staff_entry.get("points", 0))
        + get_action_score(action)
    )


def _apply_action(
    staff_id: str,
    staff_name: str,
    action: str,
    monthly_only: bool = False,
) -> None:
    if monthly_only:
        _apply_action_to_section("monthly", staff_id, staff_name, action)
    else:
        _apply_action_to_section("staff", staff_id, staff_name, action)
        _apply_action_to_section("monthly", staff_id, staff_name, action)

    app_state.leaderboard_dirty = True
    save_leaderboard_data()


def _get_raw_event_type(entry) -> str:
    raw = (
        getattr(entry, "eventType", None)
        or getattr(entry, "event_type", None)
        or getattr(entry, "eventtype", None)
        or ""
    )
    return str(raw).lower().strip()


def _get_actor(entry):
    actor = getattr(entry, "actor", None)

    if actor is not None:
        return actor.id, getattr(actor, "displayName", "Unknown")

    return getattr(entry, "actor_id", None), "Unknown"


def _get_target_id(entry):
    return (
        getattr(entry, "targetId", None)
        or getattr(entry, "target_user_id", None)
    )


def _get_target_name(entry):
    target = getattr(entry, "target", None)

    if target:
        return getattr(target, "displayName", None)

    return None


def _get_action_type(entry):
    raw = _get_raw_event_type(entry)

    if (
        "invite.accept" in raw
        or "inviteaccepted" in raw
        or "member.add" in raw
        or "member.join" in raw
        or "user.join" in raw
        or "group.member.add" in raw
        or "group.member.join" in raw
        or "group.user.join" in raw
    ):
        return "invite_accept"

    if "invite" in raw and "accept" not in raw:
        return "invite"

    if "warn" in raw:
        return "warn"

    if "kick" in raw:
        return "kick"

    if "ban" in raw:
        return "ban"

    return ""


async def _is_staff_actor(actor_id: str) -> bool:
    try:
        return await vrc_user_is_staff(str(actor_id))
    except Exception:
        return False


async def _is_staff_target(target_id: str) -> bool:
    try:
        return await vrc_user_is_staff(str(target_id))
    except Exception:
        return False


async def process_audit_log_entry(
    entry,
    monthly_only: bool = False,
):
    action = _get_action_type(entry)

    if not action:
        return False, False

    actor_id, actor_name = _get_actor(entry)

    if not actor_id:
        return False, False

    actor_id = str(actor_id)

    if not await _is_staff_actor(actor_id):
        return False, False

    target_id = _get_target_id(entry)
    target_name = _get_target_name(entry)

    target_is_staff = False

    if target_id:
        target_id = str(target_id)
        target_is_staff = await _is_staff_target(target_id)

    _ensure_staff_in_needed_sections(
        actor_id,
        actor_name,
        monthly_only=monthly_only,
    )

    if action in _MOD_ACTIONS and target_is_staff:
        return True, False

    if action == "warn" and target_id:
        add_warn(target_id, target_name or "Unknown")
    elif action == "kick" and target_id:
        add_kick(target_id, target_name or "Unknown")
    elif action == "ban" and target_id:
        add_ban(target_id, target_name or "Unknown")

    _apply_action(
        actor_id,
        actor_name,
        action,
        monthly_only=monthly_only,
    )

    return True, False


async def sync_all_vrc_staff_into_leaderboard(force_refresh: bool = False) -> int:
    added = 0
    any_changed = False
    current_staff_ids: set[str] = set()

    _ensure_section("staff")
    _ensure_archive_section()

    previous_staff_count = len(leaderboard_data.get("staff", {}))
    vrc_members = await get_all_vrc_staff_members(force_refresh=force_refresh)
    fetched_staff_count = len(vrc_members or [])

    for member in vrc_members or []:
        if not isinstance(member, dict):
            continue

        staff_id = str(
            member.get("user_id")
            or member.get("id")
            or member.get("userId")
            or ""
        ).strip()
        if not staff_id:
            continue

        current_staff_ids.add(staff_id)

        staff_name = str(
            member.get("display_name")
            or member.get("displayName")
            or member.get("name")
            or "Unknown"
        ).strip()

        was_missing_before = staff_id not in leaderboard_data.get("staff", {})

        changed = _ensure_staff_in_needed_sections(
            staff_id,
            staff_name,
            monthly_only=False,
        )

        if changed:
            any_changed = True

        if was_missing_before and staff_id in leaderboard_data.get("staff", {}):
            added += 1

    should_archive = True

    if previous_staff_count > 0:
        if fetched_staff_count == 0:
            should_archive = False
        elif fetched_staff_count < max(3, int(previous_staff_count * 0.7)):
            should_archive = False

    if should_archive:
        archived_changed = _archive_removed_staff(current_staff_ids)
        any_changed = archived_changed or any_changed
    else:
        log.warning(
            "Skipped archive pass because fetched staff count looked suspicious "
            "(previous=%s, fetched=%s)",
            previous_staff_count,
            fetched_staff_count,
        )

    if any_changed:
        app_state.leaderboard_dirty = True
        save_leaderboard_data()

    return added
