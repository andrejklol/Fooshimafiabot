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

    while action_log and action_log[0] < cutoff:
        action_log.pop(0)


# ============================================================
# TRACK ACTION
# ============================================================

async def track_high_staff_action(
    moderator_name: str,
    action_type: str,
    vrchat_user_id: str | None = None,
) -> None:
    if not HIGH_STAFF_ALERT_ENABLED:
        return

    moderator_name = str(moderator_name or "").strip() or "Unknown"
    action_type = str(action_type or "").strip().lower()

    if action_type not in {"warn", "kick", "ban"}:
        return

    threshold = _get_threshold_for_action(action_type)
    window_minutes = HIGH_STAFF_WINDOW_MINUTES

    if threshold <= 0 or window_minutes <= 0:
        return

    now = utc_now()

    recent_actions = getattr(app_state, "high_staff_recent_actions", None)
    if recent_actions is None:
        app_state.high_staff_recent_actions = {}
        recent_actions = app_state.high_staff_recent_actions

    cooldowns = getattr(app_state, "high_staff_alert_cooldowns", None)
    if cooldowns is None:
        app_state.high_staff_alert_cooldowns = {}
        cooldowns = app_state.high_staff_alert_cooldowns

    moderator_actions = recent_actions.setdefault(moderator_name, {})
    action_log = moderator_actions.setdefault(action_type, [])

    action_log.append(now)
    _prune_old_actions(action_log, window_minutes)

    count = len(action_log)
    if count < threshold:
        return

    cooldown_key = f"{moderator_name}:{action_type}"
    last_alert_time = cooldowns.get(cooldown_key)

    if last_alert_time is not None:
        cooldown_cutoff = now - timedelta(minutes=HIGH_STAFF_ALERT_COOLDOWN_MINUTES)

        if last_alert_time > cooldown_cutoff:
            log.debug(
                "high staff alert suppressed cooldown | mod=%s action=%s count=%s",
                moderator_name,
                action_type,
                count,
            )
            return

    cooldowns[cooldown_key] = now

    await send_high_staff_alert(
        bot=app_state.bot,
        moderator_name=moderator_name,
        action_type=action_type,
        count=count,
        window_minutes=window_minutes,
        threshold=threshold,
        vrchat_user_id=vrchat_user_id,
    )

    log.warning(
        "high staff threshold hit | mod=%s action=%s count=%s window=%sm threshold=%s",
        moderator_name,
        action_type,
        count,
        window_minutes,
        threshold,
    )
