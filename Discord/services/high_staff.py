import logging
from collections import defaultdict, deque
from datetime import timedelta

from core.cache import app_state
from core.utils import utc_now
from services.alerts import send_high_staff_alert


log = logging.getLogger("high_staff")


# ============================================================
# CONFIG
# ============================================================

HIGH_STAFF_THRESHOLDS = {
    "warn": {
        "threshold": 10,
        "window_minutes": 10,
    },
    "kick": {
        "threshold": 5,
        "window_minutes": 10,
    },
    "ban": {
        "threshold": 3,
        "window_minutes": 10,
    },
}


ALERT_COOLDOWN_MINUTES = 15


# ============================================================
# MEMORY STORAGE
# ============================================================

def _ensure_state():

    if not hasattr(app_state, "high_staff_actions"):
        app_state.high_staff_actions = defaultdict(lambda: defaultdict(deque))

    if not hasattr(app_state, "high_staff_last_alert"):
        app_state.high_staff_last_alert = {}


# ============================================================
# TRACK ACTION
# ============================================================

async def track_high_staff_action(
    moderator_name: str,
    action_type: str,
    vrchat_user_id: str | None = None,
):

    _ensure_state()

    action_type = str(action_type).lower().strip()

    if action_type not in HIGH_STAFF_THRESHOLDS:
        return


    config = HIGH_STAFF_THRESHOLDS[action_type]

    threshold = config["threshold"]
    window_minutes = config["window_minutes"]


    now = utc_now()

    action_log = app_state.high_staff_actions[moderator_name][action_type]

    action_log.append(now)


    # remove old entries outside window
    cutoff = now - timedelta(minutes=window_minutes)

    while action_log and action_log[0] < cutoff:
        action_log.popleft()


    count = len(action_log)


    if count < threshold:
        return


    # cooldown check
    last_alert_key = f"{moderator_name}:{action_type}"

    last_alert_time = app_state.high_staff_last_alert.get(last_alert_key)

    if last_alert_time:

        cooldown_cutoff = now - timedelta(minutes=ALERT_COOLDOWN_MINUTES)

        if last_alert_time > cooldown_cutoff:

            log.debug(
                "high staff alert suppressed cooldown | mod=%s action=%s",
                moderator_name,
                action_type,
            )

            return


    app_state.high_staff_last_alert[last_alert_key] = now


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
        "high staff threshold hit | mod=%s action=%s count=%s window=%sm",
        moderator_name,
        action_type,
        count,
        window_minutes,
    )
