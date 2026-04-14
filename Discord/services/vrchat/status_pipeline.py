import logging
import time
import uuid
from typing import Any

from core.cache import app_state

log = logging.getLogger("status_pipeline")

DEBUG_PIPELINE = False

ONLINE_USER_STATUS = {"active", "join me", "busy", "ask me"}
KNOWN_PLATFORMS = {"android", "ios", "standalonewindows", "vive", "oculus"}


# ============================================================
# BASIC HELPERS
# ============================================================

def _now_ts() -> float:
    return time.time()


def new_trace_id() -> str:
    return uuid.uuid4().hex[:8]


def log_path(stage: str, **fields: Any) -> None:
    if not DEBUG_PIPELINE:
        return

    if fields:
        extra = " ".join(f"{key}={value!r}" for key, value in fields.items())
        log.debug("status.%s %s", stage, extra)
    else:
        log.debug("status.%s", stage)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _normalize_platform(value: Any) -> str:
    return _normalize_text(value)


def _normalize_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _ensure_online_cache() -> None:
    if not hasattr(app_state, "user_online_cache") or app_state.user_online_cache is None:
        app_state.user_online_cache = {}


# ============================================================
# DECISION LOGIC
# ============================================================

def decide_online_with_reason(
    *,
    ws_online: bool | None = None,
    friend_presence: bool | None = None,
    mod_action_recent: bool | None = None,
    audit_actor_recent: bool | None = None,
    user_status: str | None = None,
    last_platform: str | None = None,
) -> tuple[bool, str]:
    """
    Priority:
    1. websocket / friend presence
    2. recent moderation / audit activity
    3. user_status only if supported by stronger context
    """

    ws_online = _normalize_bool(ws_online)
    friend_presence = _normalize_bool(friend_presence)
    mod_action_recent = _normalize_bool(mod_action_recent)
    audit_actor_recent = _normalize_bool(audit_actor_recent)

    normalized_status = _normalize_text(user_status)
    normalized_platform = _normalize_platform(last_platform)

    # --------------------------------------------------------
    # TIER 1
    # --------------------------------------------------------
    if ws_online is True:
        return True, "tier1.websocket_online"

    if ws_online is False:
        return False, "tier1.websocket_offline"

    if friend_presence is True:
        return True, "tier1.friend_presence_online"

    if friend_presence is False:
        return False, "tier1.friend_presence_offline"

    # --------------------------------------------------------
    # TIER 2
    # --------------------------------------------------------
    if mod_action_recent is True:
        return True, "tier2.mod_action_recent"

    if audit_actor_recent is True:
        return True, "tier2.audit_actor_recent"

    # --------------------------------------------------------
    # TIER 3
    # --------------------------------------------------------
    has_supported_context = any(
        [
            ws_online is not None,
            friend_presence is not None,
            mod_action_recent is True,
            audit_actor_recent is True,
            normalized_platform in KNOWN_PLATFORMS,
        ]
    )

    if normalized_status in ONLINE_USER_STATUS:
        if has_supported_context:
            return True, "tier3.user_status_supported"
        return False, "tier3.rejected_no_support"

    if normalized_status == "offline":
        if has_supported_context:
            return False, "tier3.user_status_offline_supported"
        return False, "tier3.rejected_no_support"

    return False, "no_signal"


# ============================================================
# CACHE WRITE
# ============================================================

async def process_user_status(
    *,
    user_id: str,
    ws_online: bool | None = None,
    friend_presence: bool | None = None,
    mod_action_recent: bool | None = None,
    audit_actor_recent: bool | None = None,
    user_status: str | None = None,
    last_platform: str | None = None,
) -> dict:
    cleaned_user_id = str(user_id or "").strip()
    if not cleaned_user_id:
        return {
            "user_id": "",
            "online": False,
            "reason": "missing_user_id",
            "updated_at": _now_ts(),
        }

    _ensure_online_cache()

    trace_id = new_trace_id()

    online, reason = decide_online_with_reason(
        ws_online=ws_online,
        friend_presence=friend_presence,
        mod_action_recent=mod_action_recent,
        audit_actor_recent=audit_actor_recent,
        user_status=user_status,
        last_platform=last_platform,
    )

    now = _now_ts()
    previous = app_state.user_online_cache.get(cleaned_user_id) or {}

    entry = {
        "online": online,
        "reason": reason,
        "updated_at": now,
        "user_status": _normalize_text(user_status),
        "last_platform": _normalize_platform(last_platform),
        "trace_id": trace_id,
    }

    changed = (
        previous.get("online") != entry["online"]
        or previous.get("reason") != entry["reason"]
    )

    app_state.user_online_cache[cleaned_user_id] = entry

    if changed:
        log.info(
            "status.changed user_id=%s online=%s reason=%s trace_id=%s",
            cleaned_user_id,
            entry["online"],
            entry["reason"],
            trace_id,
        )
    elif DEBUG_PIPELINE:
        log.debug(
            "status.same user_id=%s online=%s reason=%s trace_id=%s",
            cleaned_user_id,
            entry["online"],
            entry["reason"],
            trace_id,
        )

    return {
        "user_id": cleaned_user_id,
        **entry,
    }


__all__ = [
    "DEBUG_PIPELINE",
    "KNOWN_PLATFORMS",
    "ONLINE_USER_STATUS",
    "decide_online_with_reason",
    "log_path",
    "new_trace_id",
    "process_user_status",
]
