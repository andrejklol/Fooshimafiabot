import logging
from datetime import timedelta

from core.cache import app_state
from core.config import (
    HIGH_STAFF_ALERT_COOLDOWN_MINUTES,
    HIGH_STAFF_ALERT_ENABLED,
    HIGH_STAFF_BAN_THRESHOLD,
    HIGH_STAFF_KICK_THRESHOLD,
    HIGH_STAFF_WARN_THRESHOLD,
    HIGH_STAFF_WINDOW_MINUTES,
    SUSPICIOUS_ALERT_COOLDOWN_MINUTES,
    SUSPICIOUS_MOD_ENABLED,
    SUSPICIOUS_REPEAT_TARGET_THRESHOLD,
    SUSPICIOUS_REPEAT_TARGET_WINDOW_MINUTES,
    SUSPICIOUS_UNIQUE_TARGET_THRESHOLD,
    SUSPICIOUS_UNIQUE_TARGET_WINDOW_MINUTES,
)
from core.utils import utc_now
from services.alerts import send_high_staff_alert

log = logging.getLogger("high_staff")


# ============================================================
# HELPERS
# ============================================================

def _get_threshold_for_action(action_type: str) -> int:
    action_type = str(action_type or "").strip().lower()

    if action_type == "warn":
        return HIGH_STAFF_WARN_THRESHOLD

    if action_type == "kick":
        return HIGH_STAFF_KICK_THRESHOLD

    if action_type == "ban":
        return HIGH_STAFF_BAN_THRESHOLD

    return 0


def _prune_old_actions(action_log: list, window_minutes: int) -> None:
    cutoff = utc_now() - timedelta(minutes=window_minutes)

    while action_log:
        first = action_log[0]
        first_ts = first[0] if isinstance(first, tuple) else first

        if first_ts < cutoff:
            action_log.pop(0)
        else:
            break


def _get_or_create_recent_actions() -> dict:
    recent_actions = getattr(app_state, "high_staff_recent_actions", None)
    if recent_actions is None:
        app_state.high_staff_recent_actions = {}
        recent_actions = app_state.high_staff_recent_actions
    return recent_actions


def _get_or_create_cooldowns() -> dict:
    cooldowns = getattr(app_state, "high_staff_alert_cooldowns", None)
    if cooldowns is None:
        app_state.high_staff_alert_cooldowns = {}
        cooldowns = app_state.high_staff_alert_cooldowns
    return cooldowns


def _cooldown_active(cooldowns: dict, key: str, cooldown_minutes: int) -> bool:
    now = utc_now()
    last_alert_time = cooldowns.get(key)

    if last_alert_time is None:
        return False

    cutoff = now - timedelta(minutes=cooldown_minutes)
    return last_alert_time > cutoff


def _set_cooldown(cooldowns: dict, key: str) -> None:
    cooldowns[key] = utc_now()


# ============================================================
# SUSPICIOUS MOD HELPERS
# ============================================================

def _track_unique_targets(
    moderator_actions: dict,
    target_id: str,
) -> int:
    target_log = moderator_actions.setdefault("_all_targets", [])
    target_log.append((utc_now(), target_id))
    _prune_old_actions(target_log, SUSPICIOUS_UNIQUE_TARGET_WINDOW_MINUTES)

    unique_targets = {
        logged_target_id
        for _ts, logged_target_id in target_log
        if str(logged_target_id).strip()
    }
    return len(unique_targets)


def _track_repeat_target(
    moderator_actions: dict,
    target_id: str,
) -> int:
    repeat_targets = moderator_actions.setdefault("_repeat_targets", {})
    target_action_log = repeat_targets.setdefault(target_id, [])

    target_action_log.append(utc_now())
    _prune_old_actions(target_action_log, SUSPICIOUS_REPEAT_TARGET_WINDOW_MINUTES)

    return len(target_action_log)


async def _maybe_send_suspicious_unique_target_alert(
    *,
    moderator_name: str,
    discord_user_id: str | None,
    unique_target_count: int,
    cooldowns: dict,
) -> None:
    if unique_target_count < SUSPICIOUS_UNIQUE_TARGET_THRESHOLD:
        return

    cooldown_key = f"{moderator_name}:suspicious_unique_targets"
    if _cooldown_active(cooldowns, cooldown_key, SUSPICIOUS_ALERT_COOLDOWN_MINUTES):
        log.debug(
            "suspicious unique-target alert suppressed cooldown | mod=%s count=%s",
            moderator_name,
            unique_target_count,
        )
        return

    _set_cooldown(cooldowns, cooldown_key)

    await send_high_staff_alert(
        bot=app_state.bot,
        moderator_name=moderator_name,
        action_type="unique_targets",
        count=unique_target_count,
        window_minutes=SUSPICIOUS_UNIQUE_TARGET_WINDOW_MINUTES,
        threshold=SUSPICIOUS_UNIQUE_TARGET_THRESHOLD,
        discord_user_id=discord_user_id,
    )

    log.warning(
        "suspicious unique-target activity | mod=%s count=%s window=%sm threshold=%s",
        moderator_name,
        unique_target_count,
        SUSPICIOUS_UNIQUE_TARGET_WINDOW_MINUTES,
        SUSPICIOUS_UNIQUE_TARGET_THRESHOLD,
    )


async def _maybe_send_suspicious_repeat_target_alert(
    *,
    moderator_name: str,
    discord_user_id: str | None,
    target_id: str,
    repeat_target_count: int,
    cooldowns: dict,
) -> None:
    if repeat_target_count < SUSPICIOUS_REPEAT_TARGET_THRESHOLD:
        return

    cooldown_key = f"{moderator_name}:suspicious_repeat_target:{target_id}"
    if _cooldown_active(cooldowns, cooldown_key, SUSPICIOUS_ALERT_COOLDOWN_MINUTES):
        log.debug(
            "suspicious repeat-target alert suppressed cooldown | mod=%s target=%s count=%s",
            moderator_name,
            target_id,
            repeat_target_count,
        )
        return

    _set_cooldown(cooldowns, cooldown_key)

    await send_high_staff_alert(
        bot=app_state.bot,
        moderator_name=moderator_name,
        action_type=f"repeat_target:{target_id}",
        count=repeat_target_count,
        window_minutes=SUSPICIOUS_REPEAT_TARGET_WINDOW_MINUTES,
        threshold=SUSPICIOUS_REPEAT_TARGET_THRESHOLD,
        discord_user_id=discord_user_id,
    )

    log.warning(
        "suspicious repeat-target activity | mod=%s target=%s count=%s window=%sm threshold=%s",
        moderator_name,
        target_id,
        repeat_target_count,
        SUSPICIOUS_REPEAT_TARGET_WINDOW_MINUTES,
        SUSPICIOUS_REPEAT_TARGET_THRESHOLD,
    )


# ============================================================
# TRACK ACTION
# ============================================================

async def track_high_staff_action(
    moderator_name: str,
    action_type: str,
    discord_user_id: str | None = None,
    target_id: str | None = None,
    target_name: str | None = None,
) -> None:
    if not HIGH_STAFF_ALERT_ENABLED:
        return

    moderator_name = str(moderator_name or "").strip() or "Unknown"
    action_type = str(action_type or "").strip().lower()
    discord_user_id = str(discord_user_id or "").strip() or None
    target_id = str(target_id or "").strip() or None
    target_name = str(target_name or "").strip() or None

    if action_type not in {"warn", "kick", "ban"}:
        return

    threshold = _get_threshold_for_action(action_type)
    window_minutes = HIGH_STAFF_WINDOW_MINUTES

    if threshold <= 0 or window_minutes <= 0:
        return

    now = utc_now()

    recent_actions = _get_or_create_recent_actions()
    cooldowns = _get_or_create_cooldowns()

    moderator_actions = recent_actions.setdefault(moderator_name, {})
    action_log = moderator_actions.setdefault(action_type, [])

    action_log.append(now)
    _prune_old_actions(action_log, window_minutes)

    count = len(action_log)

    if count >= threshold:
        cooldown_key = f"{moderator_name}:{action_type}"

        if _cooldown_active(
            cooldowns,
            cooldown_key,
            HIGH_STAFF_ALERT_COOLDOWN_MINUTES,
        ):
            log.debug(
                "high staff alert suppressed cooldown | mod=%s action=%s count=%s",
                moderator_name,
                action_type,
                count,
            )
        else:
            _set_cooldown(cooldowns, cooldown_key)

            await send_high_staff_alert(
                bot=app_state.bot,
                moderator_name=moderator_name,
                action_type=action_type,
                count=count,
                window_minutes=window_minutes,
                threshold=threshold,
                discord_user_id=discord_user_id,
            )

            log.warning(
                "high staff threshold hit | mod=%s action=%s count=%s window=%sm threshold=%s",
                moderator_name,
                action_type,
                count,
                window_minutes,
                threshold,
            )

    if not SUSPICIOUS_MOD_ENABLED:
        return

    if not target_id:
        return

    unique_target_count = _track_unique_targets(moderator_actions, target_id)
    repeat_target_count = _track_repeat_target(moderator_actions, target_id)

    await _maybe_send_suspicious_unique_target_alert(
        moderator_name=moderator_name,
        discord_user_id=discord_user_id,
        unique_target_count=unique_target_count,
        cooldowns=cooldowns,
    )

    await _maybe_send_suspicious_repeat_target_alert(
        moderator_name=moderator_name,
        discord_user_id=discord_user_id,
        target_id=target_name or target_id,
        repeat_target_count=repeat_target_count,
        cooldowns=cooldowns,
    )
