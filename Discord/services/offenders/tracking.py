from __future__ import annotations

from typing import Any

# Cross-module import to hook into your global name lookup layer
from ..leaderboard.processors import resolve_username
from .storage import repeat_offenders, save_repeat_offenders, _mark_dirty

WARN_THRESHOLD = 3
KICK_THRESHOLD = 2
BAN_THRESHOLD = 1

WARN_WINDOW_DAYS = 7
KICK_WINDOW_DAYS = 30
BAN_WINDOW_DAYS = 30


def _ensure_user(user_id: str, username: str | None = None) -> dict[str, Any]:
    user_id = str(user_id)
    resolved_name = resolve_username(user_id, username)

    if user_id not in repeat_offenders or not isinstance(repeat_offenders[user_id], dict):
        repeat_offenders[user_id] = {
            "name": resolved_name,
            "warn": 0,
            "kick": 0,
            "ban": 0,
        }
    else:
        # Gracefully preserve names or pull down from active leaderboard cache overrides
        entry_name = repeat_offenders[user_id].get("name")
        if not entry_name or entry_name.startswith("User "):
            repeat_offenders[user_id]["name"] = resolved_name

    _mark_dirty()
    return repeat_offenders[user_id]


def increment_offence(
    user_id: str, 
    username: str | None, 
    offence_type: str, 
    save: bool = True,
    notify: bool = True
) -> dict[str, Any]:
    """
    Central mutation entrypoint. Increments infractions safely, handles dirty flags,
    and returns the updated offender entry dictionary.
    """
    if offence_type not in {"warn", "kick", "ban"}:
        raise ValueError(f"Unsupported infraction categorization type: {offence_type}")

    user_id = str(user_id)
    entry = _ensure_user(user_id, username)
    
    try:
        entry[offence_type] = int(entry.get(offence_type, 0)) + 1
    except Exception:
        entry[offence_type] = 1

    if save:
        save_repeat_offenders()
    else:
        _mark_dirty()

    # The notify flag gives you a safety switch to bypass alert execution loops 
    # when processing internal batch migrations, offline scripts, or multi-user actions.
    if notify:
        try:
            from services.alerts import send_repeat_alert
            triggered = get_triggered_thresholds(user_id)
            if triggered:
                highest = get_highest_action(triggered)
                # Invokes the external alert subsystem hook safely
                send_repeat_alert(user_id, entry.get("name"), highest, triggered)
        except Exception:
            pass # Keep core tracker isolated from external notification network failures

    return entry


def add_warn(user_id: str, username: str | None, save: bool = True, notify: bool = True) -> dict[str, Any]:
    return increment_offence(user_id, username, "warn", save=save, notify=notify)


def add_kick(user_id: str, username: str | None, save: bool = True, notify: bool = True) -> dict[str, Any]:
    return increment_offence(user_id, username, "kick", save=save, notify=notify)


def add_ban(user_id: str, username: str | None, save: bool = True, notify: bool = True) -> dict[str, Any]:
    return increment_offence(user_id, username, "ban", save=save, notify=notify)


def get_repeat_offender_data(user_id: str) -> dict[str, Any] | None:
    data = repeat_offenders.get(str(user_id))
    return data if isinstance(data, dict) else None


def get_triggered_thresholds(user_id: str) -> list[tuple[str, int, int, int]]:
    data = get_repeat_offender_data(user_id)
    if not data:
        return []

    triggered: list[tuple[str, int, int, int]] = []

    def _safe_get(k: str) -> int:
        try:
            return max(0, int(data.get(k, 0)))
        except Exception:
            return 0

    warn_count = _safe_get("warn")
    kick_count = _safe_get("kick")
    ban_count = _safe_get("ban")

    if warn_count >= WARN_THRESHOLD:
        triggered.append(("warn", warn_count, WARN_WINDOW_DAYS, WARN_THRESHOLD))

    if kick_count >= KICK_THRESHOLD:
        triggered.append(("kick", kick_count, KICK_WINDOW_DAYS, KICK_THRESHOLD))

    if ban_count >= BAN_THRESHOLD:
        triggered.append(("ban", ban_count, BAN_WINDOW_DAYS, BAN_THRESHOLD))

    return triggered


def get_highest_action(target: str | list[tuple[str, int, int, int]]) -> str:
    """
    Evaluates thresholds and returns the highest severe action category ('ban', 'kick', 'warn').
    Accepts either a string user_id or a list of pre-compiled threshold tuples.
    """
    if isinstance(target, str):
        triggered = get_triggered_thresholds(target)
    else:
        triggered = target

    actions = {action for action, *_ in triggered}
    if "ban" in actions:
        return "ban"
    if "kick" in actions:
        return "kick"
    if "warn" in actions:
        return "warn"
    return ""


def is_repeat_offender(user_id: str) -> bool:
    return bool(get_triggered_thresholds(user_id))
