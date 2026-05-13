"""
status_pipeline.py

Single source of truth for presence reconciliation and cached online status.

This module should be the only place that decides:
- whether a VRChat user is considered online
- why they were considered online/offline
- how that decision is written into app_state.user_online_cache

vrchat_presence.py may import from here — NOT the other way around.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from core.cache import app_state

log = logging.getLogger("status_pipeline")

DEBUG_PIPELINE = False
PIPELINE_STALE_SECONDS = 180

ONLINE_USER_STATUS = {"active", "join me", "busy", "ask me"}
KNOWN_PLATFORMS = {"android", "ios", "standalonewindows", "vive", "oculus"}

__all__ = [
    "DEBUG_PIPELINE",
    "ONLINE_USER_STATUS",
    "KNOWN_PLATFORMS",
    "new_trace_id",
    "log_path",
    "process_user_status",
    "decide_online_with_reason",
]


# ============================================================
# INTERNAL HELPERS
# ============================================================

def _now_ts() -> float:
    return time.time()


def new_trace_id() -> str:
    return uuid.uuid4().hex[:8]


def log_path(stage: str, trace_id: str | None = None, **fields: Any) -> None:
    """
    Lightweight structured debug logger for pipeline decision tracing.
    Only emits when DEBUG_PIPELINE is enabled.
    """
    if not DEBUG_PIPELINE:
        return

    tid = trace_id or new_trace_id()
    extras = " ".join(f"{k}={fields[k]!r}" for k in sorted(fields))
    log.debug("[trace=%s] %s %s", tid, stage, extras)


def _normalize_platform(value: str | None) -> str:
    return str(value or "").strip().lower()


def _normalize_status(value: str | None) -> str:
    return str(value or "").strip().lower()


def _is_supported_user_status(value: str | None) -> bool:
    return _normalize_status(value) in ONLINE_USER_STATUS


def _has_known_platform(value: str | None) -> bool:
    return _normalize_platform(value) in KNOWN_PLATFORMS


def _ensure_pipeline_presence_cache() -> dict[str, dict[str, Any]]:
    if not hasattr(app_state, "vrc_pipeline_friend_presence") or app_state.vrc_pipeline_friend_presence is None:
        app_state.vrc_pipeline_friend_presence = {}
    return app_state.vrc_pipeline_friend_presence


def _ensure_user_online_cache() -> dict[str, dict[str, Any]]:
    if not hasattr(app_state, "user_online_cache") or app_state.user_online_cache is None:
        app_state.user_online_cache = {}
    return app_state.user_online_cache


def _set_pipeline_presence_simple(
    uid: str,
    online: bool,
    platform: str | None,
) -> None:
    cache = _ensure_pipeline_presence_cache()
    old = cache.get(uid, {})

    cache[uid] = {
        **old,
        "online": bool(online),
        "platform": _normalize_platform(platform),
        "updated_at": _now_ts(),
    }

    app_state.vrc_pipeline_last_event_ts = _now_ts()


def _get_pipeline_presence_simple(uid: str) -> dict[str, Any] | None:
    if not hasattr(app_state, "vrc_pipeline_friend_presence"):
        return None

    entry = (app_state.vrc_pipeline_friend_presence or {}).get(uid)
    if not entry:
        return None

    ts = float(entry.get("updated_at", 0) or 0)
    if (_now_ts() - ts) >= PIPELINE_STALE_SECONDS:
        return None

    return entry


def _pipeline_cache_is_stale_simple() -> bool:
    last = float(getattr(app_state, "vrc_pipeline_last_event_ts", 0.0) or 0.0)
    return last <= 0 or (_now_ts() - last) >= PIPELINE_STALE_SECONDS


# ============================================================
# DECISION ENGINE
# ============================================================

async def decide_online_with_reason(
    user_id: str,
    ws_online: bool | None = None,
    friend_presence: bool | None = None,
    mod_action_recent: bool = False,
    audit_actor_recent: bool = False,
    user_status: str | None = None,
    last_platform: str | None = None,
) -> tuple[bool, str]:
    """
    Decide whether a user should be considered online.
    """
    uid = str(user_id or "").strip()
    trace_id = new_trace_id()

    status_norm = _normalize_status(user_status)
    platform_norm = _normalize_platform(last_platform)
    status_supported = _is_supported_user_status(status_norm)

    log_path(
        "decide.start",
        trace_id,
        uid=uid,
        ws_online=ws_online,
        friend_presence=friend_presence,
        mod_action_recent=mod_action_recent,
        audit_actor_recent=audit_actor_recent,
        user_status=status_norm,
        last_platform=platform_norm,
    )

    # -------------------------
    # Tier 1: strongest signals
    # -------------------------
    if ws_online is True:
        log_path("decide.return", trace_id, online=True, reason="tier1.websocket_online")
        return True, "tier1.websocket_online"

    if friend_presence is True:
        log_path("decide.return", trace_id, online=True, reason="tier1.friend_presence_online")
        return True, "tier1.friend_presence_online"

    # Cached pipeline presence handles positive states safely
    if uid:
        entry = _get_pipeline_presence_simple(uid)
        if entry and not _pipeline_cache_is_stale_simple() and entry.get("online"):
            cached_platform = _normalize_platform(entry.get("platform"))
            log_path("decide.return", trace_id, online=True, reason="tier1.pipeline_cache_online")
            return True, "tier1.pipeline_cache_online"

    # -------------------------
    # Tier 2: recent activity
    # -------------------------
    if mod_action_recent:
        log_path("decide.return", trace_id, online=True, reason="tier2.mod_action_recent")
        return True, "tier2.mod_action_recent"

    if audit_actor_recent:
        log_path("decide.return", trace_id, online=True, reason="tier2.audit_actor_recent")
        return True, "tier2.audit_actor_recent"

    # -------------------------
    # Tier 3: user_status
    # -------------------------
    if status_norm:
        if status_norm == "offline":
            log_path("decide.return", trace_id, online=False, reason="tier3.explicit_offline")
            return False, "tier3.explicit_offline"
            
        if not status_supported:
            log_path("decide.return", trace_id, online=False, reason=f"tier3.rejected_status:{status_norm}")
            return False, f"tier3.rejected_status:{status_norm}"

        # If any strong signal confirms they are offline, trust it over a sticky API status string
        if ws_online is False:
            log_path("decide.return", trace_id, online=False, reason="tier3.status_overridden_by_ws_offline")
            return False, "tier3.status_overridden_by_ws_offline"
            
        if friend_presence is False and not ws_online:
            log_path("decide.return", trace_id, online=False, reason="tier3.status_overridden_by_friend_offline")
            return False, "tier3.status_overridden_by_friend_offline"

        # Otherwise, if the profile explicitly declares an active status state, they are online!
        log_path("decide.return", trace_id, online=True, reason=f"tier3.user_status:{status_norm}")
        return True, f"tier3.user_status:{status_norm}"

    # -------------------------
    # No usable signal
    # -------------------------
    log_path("decide.return", trace_id, online=False, reason="no_signal")
    return False, "no_signal"


# ============================================================
# PROCESS USER STATUS
# ============================================================

async def process_user_status(
    user_id: str,
    ws_online: bool | None = None,
    friend_presence: bool | None = None,
    mod_action_recent: bool = False,
    audit_actor_recent: bool = False,
    user_status: str | None = None,
    last_platform: str | None = None,
) -> None:
    """
    Reconcile all available signals and write the final result into
    app_state.user_online_cache[user_id].
    """
    uid = str(user_id or "").strip()
    if not uid:
        return

    trace_id = new_trace_id()
    platform_norm = _normalize_platform(last_platform)

    if ws_online is not None or friend_presence is not None:
        derived_online = bool(ws_online) if ws_online is not None else bool(friend_presence)
        _set_pipeline_presence_simple(uid, derived_online, platform_norm)

    online, reason = await decide_online_with_reason(
        user_id=uid,
        ws_online=ws_online,
        friend_presence=friend_presence,
        mod_action_recent=mod_action_recent,
        audit_actor_recent=audit_actor_recent,
        user_status=user_status,
        last_platform=platform_norm,
    )

    cache = _ensure_user_online_cache()
    old = cache.get(uid)

    new_entry = {
        "online": bool(online),
        "reason": str(reason),
        "updated_at": _now_ts(),
    }
    cache[uid] = new_entry

    if old is None:
        log.info(
            "status.initial uid=%s online=%s reason=%s platform=%s",
            uid,
            new_entry["online"],
            new_entry["reason"],
            platform_norm or "unknown",
        )
        return

    changed = (
        bool(old.get("online")) != new_entry["online"]
        or str(old.get("reason", "")) != new_entry["reason"]
    )

    if changed:
        log.info(
            "status.changed uid=%s online=%s->%s reason=%s->%s platform=%s",
            uid,
            bool(old.get("online")),
            new_entry["online"],
            str(old.get("reason", "")),
            new_entry["reason"],
            platform_norm or "unknown",
        )
    else:
        log_path(
            "status.same",
            trace_id,
            uid=uid,
            online=new_entry["online"],
            reason=new_entry["reason"],
            platform=platform_norm or "unknown",
        )
