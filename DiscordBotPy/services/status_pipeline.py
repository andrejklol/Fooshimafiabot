import logging
import uuid
from datetime import timedelta

from core.cache import app_state
from core.utils import utc_now

log = logging.getLogger("status_pipeline")


# ============================================================
# TRACE HELPERS
# ============================================================

DEBUG_PIPELINE = False   # toggle detailed logs


def new_trace_id() -> str:
    return uuid.uuid4().hex[:8]


def log_path(trace_id: str, step: str, level="debug", **fields):
    if not DEBUG_PIPELINE and level == "debug":
        return

    parts = [f"[trace={trace_id}]", f"[step={step}]"]

    for k, v in fields.items():
        parts.append(f"{k}={v}")

    message = " ".join(parts)

    if level == "info":
        log.info(message)
    elif level == "warning":
        log.warning(message)
    else:
        log.debug(message)


# ============================================================
# CONSTANTS
# ============================================================

ONLINE_USER_STATUS = {
    "active",
    "join me",
    "busy",
    "ask me",
}

KNOWN_PLATFORMS = {
    "android",
    "ios",
    "standalonewindows",
    "vive",
    "oculus",
}


# ============================================================
# SIGNAL EVALUATION
# ============================================================

def decide_online_with_reason(signals: dict) -> tuple[bool, str]:

    ws_online = signals.get("ws_online")
    friend_presence = signals.get("friend_presence")
    mod_action_recent = signals.get("mod_action_recent")
    audit_actor_recent = signals.get("audit_actor_recent")
    user_status = (signals.get("user_status") or "").strip().lower()
    last_platform = (signals.get("last_platform") or "").strip().lower()

    # Tier 1
    if ws_online is True:
        return True, "tier1.websocket"

    if friend_presence is True:
        return True, "tier1.friend_presence"

    # Tier 2
    if mod_action_recent:
        return True, "tier2.mod_action"

    if audit_actor_recent:
        return True, "tier2.audit_actor"

    # Tier 3
    if user_status in ONLINE_USER_STATUS:

        supporting_hint = (
                ws_online is True
                or friend_presence is True
                or mod_action_recent
                or audit_actor_recent
                or last_platform in KNOWN_PLATFORMS
        )

        if supporting_hint:
            return True, "tier3.user_status_supported"

        return False, "tier3.rejected_no_support"

    return False, "no_signal"


# ============================================================
# MAIN PIPELINE ENTRY
# ============================================================

async def process_user_status(
        user_id: str,
        ws_online: bool | None = None,
        friend_presence: bool | None = None,
        mod_action_recent: bool | None = None,
        audit_actor_recent: bool | None = None,
        user_status: str | None = None,
        last_platform: str | None = None,
):

    trace_id = new_trace_id()

    signals = {
        "ws_online": ws_online,
        "friend_presence": friend_presence,
        "mod_action_recent": mod_action_recent,
        "audit_actor_recent": audit_actor_recent,
        "user_status": user_status,
        "last_platform": last_platform,
    }

    final_online, reason = decide_online_with_reason(signals)

    previous = app_state.user_online_cache.get(user_id)

    app_state.user_online_cache[user_id] = {
        "online": final_online,
        "reason": reason,
        "updated_at": utc_now(),
    }

    # ========================================================
    # ONLY LOG WHEN STATE CHANGES
    # ========================================================

    if previous is None:

        log_path(
            trace_id,
            "status.initial",
            level="info",
            user_id=user_id,
            online=final_online,
            reason=reason,
        )

    elif previous["online"] != final_online:

        log_path(
            trace_id,
            "status.changed",
            level="info",
            user_id=user_id,
            old=previous["online"],
            new=final_online,
            reason=reason,
        )

    else:

        log_path(
            trace_id,
            "status.same",
            user_id=user_id,
            online=final_online,
            reason=reason,
        )

    return final_online