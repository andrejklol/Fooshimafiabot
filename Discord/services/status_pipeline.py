import logging
import uuid

from core.cache import app_state
from core.utils import utc_now

log = logging.getLogger("status_pipeline")


# ============================================================
# TRACE HELPERS
# ============================================================

DEBUG_PIPELINE = False  # toggle detailed logs


def new_trace_id() -> str:
    return uuid.uuid4().hex[:8]


def log_path(trace_id: str, step: str, level: str = "debug", **fields) -> None:
    if not DEBUG_PIPELINE and level == "debug":
        return

    parts = [f"[trace={trace_id}]", f"[step={step}]"]
    parts.extend(f"{key}={value}" for key, value in fields.items())
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
    "web",  # allows active+web to count as supported
}


# ============================================================
# SIGNAL EVALUATION
# ============================================================

def _normalized_signal_text(value) -> str:
    return str(value or "").strip().lower()


def decide_online_with_reason(signals: dict) -> tuple[bool, str]:
    ws_online = signals.get("ws_online")
    friend_presence = signals.get("friend_presence")
    mod_action_recent = signals.get("mod_action_recent")
    audit_actor_recent = signals.get("audit_actor_recent")
    user_status = _normalized_signal_text(signals.get("user_status"))
    last_platform = _normalized_signal_text(signals.get("last_platform"))

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

def _build_signal_snapshot(
    *,
    ws_online: bool | None,
    friend_presence: bool | None,
    mod_action_recent: bool | None,
    audit_actor_recent: bool | None,
    user_status: str | None,
    last_platform: str | None,
) -> dict:
    return {
        "ws_online": ws_online,
        "friend_presence": friend_presence,
        "mod_action_recent": mod_action_recent,
        "audit_actor_recent": audit_actor_recent,
        "user_status": user_status,
        "last_platform": last_platform,
    }


def _build_cache_entry(online: bool, reason: str) -> dict:
    return {
        "online": online,
        "reason": reason,
        "updated_at": utc_now(),
    }


def _log_status_result(
    *,
    trace_id: str,
    previous: dict | None,
    user_id: str,
    final_online: bool,
    reason: str,
    signals: dict,
) -> None:
    fields = {
        "user_id": user_id,
        "reason": reason,
        **signals,
    }

    if previous is None:
        log_path(
            trace_id,
            "status.initial",
            level="info",
            online=final_online,
            **fields,
        )
        return

    if previous.get("online") != final_online:
        log_path(
            trace_id,
            "status.changed",
            level="info",
            old=previous.get("online"),
            new=final_online,
            **fields,
        )
        return

    log_path(
        trace_id,
        "status.same",
        online=final_online,
        **fields,
    )


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

    signals = _build_signal_snapshot(
        ws_online=ws_online,
        friend_presence=friend_presence,
        mod_action_recent=mod_action_recent,
        audit_actor_recent=audit_actor_recent,
        user_status=user_status,
        last_platform=last_platform,
    )

    final_online, reason = decide_online_with_reason(signals)
    previous = app_state.user_online_cache.get(user_id)

    app_state.user_online_cache[user_id] = _build_cache_entry(
        online=final_online,
        reason=reason,
    )

    _log_status_result(
        trace_id=trace_id,
        previous=previous,
        user_id=user_id,
        final_online=final_online,
        reason=reason,
        signals=signals,
    )

    return final_online
