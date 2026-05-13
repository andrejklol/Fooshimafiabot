from __future__ import annotations

import logging
from datetime import timezone
from typing import Any, Optional

from core.cache import app_state
from core.config import GROUP_ID, RECENT_LOG_FETCH_COUNT
from core.utils import run_blocking, send_error_log

from services.tasks.autosave import autosave_if_dirty
from services.tasks.monthly_reset import check_monthly_reset

log = logging.getLogger("bot.vrchat_audit_dispatcher")

# ─── Event Type Classification ────────────────────────────────────────────

MODERATION_EVENT_TYPES = {
    "group.instance.warn", "group.instance.kick", "group.instance.ban",
    "group.user.ban", "group.member.ban", "group.member.kick",
    "group.member.warn", "group.member.unban", "group.invite.create",
    "group.invite.accept",
}

LIFECYCLE_TO_EVENT = {
    "group.instance.create": "instance_opened",
    "group.instance.open":   "instance_opened",
    "group.instance.update": "instance_opened",
    "group.instance.delete": "instance_closed",
    "group.instance.close":  "instance_closed",
    "group.member.request":      "group_join_requested",
    "group.join.request":        "group_join_requested",
    "group.member.join_request": "group_join_requested",
}

# vrchatapi SDK model attrs we never want to forward — they're SDK
# internals that leak in via `__dict__` traversal.
_SKIP_ATTRS = {"local_vars_configuration", "discriminator"}

# Field-name normalization → dashboard's canonical snake_case slots.
_REST_RENAME = {
    # ── Actor ─────────────────────────────────────────────────────────
    "actorId":                           "actor_vrchat_id",
    "actor_id":                          "actor_vrchat_id",
    "actorDisplayName":                  "actor_vrchat_name",
    "actor_display_name":                "actor_vrchat_name",
    "currentAvatarThumbnailImageUrl":    "actor_avatar_url",
    "current_avatar_thumbnail_image_url": "actor_avatar_url",

    # ── Target ────────────────────────────────────────────────────────
    "targetId":                          "target_vrchat_id",
    "target_id":                         "target_vrchat_id",
    "targetUserId":                      "target_vrchat_id",
    "target_user_id":                    "target_vrchat_id",
    "targetDisplayName":                 "target_vrchat_name",
    "target_display_name":               "target_vrchat_name",
    "targetUserDisplayName":             "target_vrchat_name",
    "target_user_display_name":          "target_vrchat_name",

    # ── Instance / world / group context ─────────────────────────────
    "instanceId":   "instance_id",
    "instanceName": "instance_name",
    "worldId":      "world_id",
    "worldName":    "world_name",
    "groupId":      "group_id",
    "groupName":    "group_name",

    # ── Description → details ────────────────────────────────────────
    # VRChat's audit log already provides a fully-formed sentence in
    # the `description` field for moderation events.
    "description":  "details",
    "message":      "details",
}

# ─── Human-readable summary builder (Fallback) ───────────────────────

_DETAILS_TEMPLATES = {
    "group.invite.create":   "{actor} sent a group invite to {target}.",
    "group.invite.accept":   "{target} accepted {actor}'s group invite.",
    "group.invite.cancel":   "{actor} cancelled the invite to {target}.",
    "group.invite.revoke":   "{actor} revoked the invite to {target}.",
    "group.invite.expire":   "Invite from {actor} to {target} expired.",
    "group.invite.decline":  "{target} declined {actor}'s group invite.",
    "group.instance.create": "Group instance opened by {actor}.",
    "group.instance.open":   "Group instance opened by {actor}.",
    "group.instance.delete": "Group instance closed by {actor}.",
    "group.instance.close":  "Group instance closed by {actor}.",
    "group.member.join":     "{actor} joined the group.",
    "group.member.leave":    "{actor} left the group.",
    "group.member.remove":   "{actor} was removed from the group.",
    "group.member.add":      "{actor} was added to the group.",
    "group.member.request":      "{actor} requested to join the group.",
    "group.join.request":        "{actor} requested to join the group.",
    "group.member.join_request": "{actor} requested to join the group.",
    "group.role.assign":     "Role assigned to {target} by {actor}.",
    "group.role.unassign":   "Role removed from {target} by {actor}.",
}

def _build_details_summary(raw_type: str, payload: dict) -> Optional[str]:
    """Return a sentence-style summary as a backstop if VRChat description is missing."""
    template = _DETAILS_TEMPLATES.get(raw_type)
    if not template:
        return None
    actor = (payload.get("actor_vrchat_name") or "").strip() or "System"
    target = (payload.get("target_vrchat_name") or "").strip() or "an unknown user"
    try:
        return template.format(actor=actor, target=target)
    except (KeyError, IndexError):
        return None

# ─── Internal Helpers ─────────────────────────────────────────────────────

def _normalize_utc(dt):
    if not dt: return None
    if getattr(dt, "tzinfo", None) is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def _normalize_payload(entry_obj: Any) -> dict:
    """Extracts attributes, flattens `data`, renames to dashboard fields."""
    if isinstance(entry_obj, dict):
        raw = dict(entry_obj)
    else:
        raw = {}
        for k, v in entry_obj.__dict__.items():
            if k.startswith("__"):
                continue
            if not k.startswith("_"):
                continue
            normalized_key = k[1:]
            if normalized_key in _SKIP_ATTRS:
                continue
            raw[normalized_key] = v

    data = getattr(entry_obj, "data", raw.get("data", {}))
    if isinstance(data, dict):
        for k, v in data.items():
            raw.setdefault(k, v)

    cleaned = {}
    for k, v in raw.items():
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        cleaned[_REST_RENAME.get(k, k)] = v

    created_at = getattr(entry_obj, "created_at", raw.get("created_at"))
    if created_at:
        norm = _normalize_utc(created_at)
        if norm:
            cleaned["created_at"] = norm.isoformat()
            cleaned.setdefault("timestamp", cleaned["created_at"])

    entry_id = getattr(entry_obj, "id", raw.get("id"))
    if entry_id:
        cleaned.setdefault("external_id", str(entry_id))

    return cleaned

async def dispatch_audit_log_row(entry: Any, *, sync) -> Optional[dict]:
    """Routes an audit entry to the dashboard based on event type."""
    raw_type = (
        getattr(entry, "event_type", None) or
        getattr(entry, "eventType", None) or
        (entry.get("eventType") if isinstance(entry, dict) else "") or ""
    ).strip()

    if not raw_type:
        return None

    payload = _normalize_payload(entry)

    # Use VRChat's native description if available; otherwise use our fallback templates.
    if not payload.get("details"):
        summary = _build_details_summary(raw_type, payload)
        if summary:
            payload["details"] = summary

    try:
        # 1. MODERATION
        if raw_type in MODERATION_EVENT_TYPES:
            return await sync.push_log(action_type=raw_type, category="moderation", **payload)

        # 2. INSTANCE LIFECYCLE / JOIN REQUEST
        event_type = LIFECYCLE_TO_EVENT.get(raw_type)
        if event_type and hasattr(sync, "push_vrchat_activity"):
            return await sync.push_vrchat_activity(event_type=event_type, **payload)

        # 3. INFO / MEMBERSHIP / UNKNOWN
        return await sync.push_log(action_type=raw_type, category="info", **payload)

    except Exception:
        log.exception("vrchat_audit dispatch failed for type: %s", raw_type)
        return None

# ─── Main Polling Task ────────────────────────────────────────────────────

async def check_logs_once() -> None:
    if not app_state.vrc_groups_api or not app_state.startup_timestamp:
        return

    try:
        await check_monthly_reset()

        logs = await run_blocking(
            app_state.vrc_groups_api.get_group_audit_logs,
            group_id=GROUP_ID,
            n=RECENT_LOG_FETCH_COUNT,
            offset=0,
        )
        results = getattr(logs, "results", []) or []
        startup_ts = _normalize_utc(app_state.startup_timestamp)

        new_logs = []
        for entry in results:
            log_id = getattr(entry, "id", None)
            created_at = _normalize_utc(getattr(entry, "created_at", None))

            if not created_at or not log_id:
                continue
            if log_id in app_state.processed_log_ids:
                continue
            if startup_ts and created_at < startup_ts:
                continue

            new_logs.append(entry)

        if not new_logs:
            return

        ordered = list(reversed(new_logs))
        sync_client = getattr(app_state, "dashboard_sync", None)

        for entry in ordered:
            if sync_client:
                res = await dispatch_audit_log_row(entry, sync=sync_client)
                if res and not res.get("success"):
                    log.warning("Dashboard sync failed for log %s", getattr(entry, "id", "??"))

            app_state.processed_log_ids.add(getattr(entry, "id"))

        latest_entry_ts = _normalize_utc(getattr(ordered[-1], "created_at", None))
        if latest_entry_ts:
            app_state.last_log_received_at = latest_entry_ts

        await autosave_if_dirty()

    except Exception as exc:
        await send_error_log("Log Polling Error", exc)
