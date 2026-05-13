from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("bot.vrchat_audit_dispatcher")


# ─── Event type maps ─────────────────────────────────────────────────────
#
# VRChat's group audit-log uses dotted event names. We classify each into
# one of four buckets so the dashboard can route correctly.
#
# Add new entries here when VRChat introduces variants. The dashboard side
# is fully tolerant — anything unknown lands as an `info` row in the audit
# panel for triage.
# -----------------------------------------------------------------------

# Moderation events — route via push_log (counts on charts, bumps counters).
MODERATION_EVENT_TYPES = {
    "group.instance.warn",
    "group.instance.kick",
    "group.instance.ban",
    "group.user.ban",
    "group.member.ban",
    "group.member.kick",
    "group.member.warn",
    "group.member.unban",
    "group.invite.create",
    "group.invite.accept",
}

# Instance lifecycle — route via push_vrchat_activity (informational).
LIFECYCLE_TO_EVENT = {
    "group.instance.create": "instance_opened",
    "group.instance.open":   "instance_opened",
    "group.instance.update": "instance_opened",   # treat updates as fresh open beacon
    "group.instance.delete": "instance_closed",
    "group.instance.close":  "instance_closed",
    "group.member.request":     "group_join_requested",
    "group.join.request":       "group_join_requested",
    "group.member.join_request":"group_join_requested",
}

# Membership / role changes — currently route via push_log as info rows
# (no typed endpoint yet). Dashboard's `_SYSTEM_ACTION_TYPES` set classifies
# these as system events so they render muted, not yellow "unmapped".
MEMBERSHIP_EVENT_TYPES = {
    "group.member.join",
    "group.member.leave",
    "group.member.remove",
    "group.member.add",
    "group.role.assign",
    "group.role.unassign",
    "group.role.create",
    "group.role.delete",
    "group.role.update",
    "group.update",
    "group.post.create",
    "group.post.delete",
    "group.announcement.create",
    # Invite-flow noise — NOT real moderation (no user actually punished).
    # These represent the bot/staff cancelling a stale outgoing invite or
    # the system auto-expiring one, so they route as info rows (no chart
    # bucket, no counter bump). Dashboard's _SYSTEM_ACTION_TYPES marks
    # them as system so the audit panel keeps them muted, not yellow.
    "group.invite.cancel",
    "group.invite.revoke",
    "group.invite.expire",
    "group.invite.decline",
}


# ─── Field-name normalization ────────────────────────────────────────────
#
# VRChat's REST API uses camelCase; bot-side helpers use snake_case. We
# rename here so the typed `push_*` helpers and the generic `push_log`
# both receive the field names they expect.

_REST_RENAME = {
    "actorId":                          "actor_vrchat_id",
    "actorDisplayName":                 "actor_vrchat_name",
    "currentAvatarThumbnailImageUrl":   "actor_avatar_url",
    "instanceId":                       "instance_id",
    "instanceName":                     "instance_name",
    "worldId":                          "world_id",
    "worldName":                        "world_name",
    "groupId":                          "group_id",
    "groupName":                        "group_name",
    "targetId":                         "target_vrchat_id",
    "targetDisplayName":                "target_vrchat_name",
}


def _normalize_payload(row: dict) -> dict:
    """Flatten `data: {...}` into the row, drop empties, and rename camelCase
    fields. Returns a flat dict the dashboard helpers can consume directly."""
    if not isinstance(row, dict):
        return {}
    flat = dict(row)
    data = row.get("data")
    if isinstance(data, dict):
        for k, v in data.items():
            flat.setdefault(k, v)
    cleaned = {}
    for k, v in flat.items():
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        cleaned[_REST_RENAME.get(k, k)] = v
    return cleaned


# ─── Public dispatcher ───────────────────────────────────────────────────


async def dispatch_audit_log_row(row: Any, *, sync) -> Optional[dict]:
    """Route a single VRChat group audit-log entry to the dashboard.

    Audit-log row shape (from VRChat REST API):
        {
          "id": "...",
          "eventType": "group.instance.create",
          "actorId": "usr_...",
          "actorDisplayName": "...",
          "data": { "instanceId": "...", ... }
        }

    Returns the dashboard response dict on success; None on parse error
    or when the dashboard returns non-success. NEVER raises — callers can
    invoke this in a tight poll loop without their own try/except.
    """
    if not isinstance(row, dict):
        return None
    raw_type = (row.get("eventType") or row.get("event_type") or "").strip()
    if not raw_type:
        return None

    payload = _normalize_payload(row)

    try:
        # ── MODERATION ────────────────────────────────────────────────
        if raw_type in MODERATION_EVENT_TYPES:
            return await _dispatch_moderation(raw_type, payload, sync)

        # ── INSTANCE LIFECYCLE / JOIN REQUESTS ────────────────────────
        event_type = LIFECYCLE_TO_EVENT.get(raw_type)
        if event_type:
            return await _dispatch_lifecycle(event_type, raw_type, payload, sync)

        # ── MEMBERSHIP / ROLE / OTHER GROUP EVENTS ────────────────────
        if raw_type in MEMBERSHIP_EVENT_TYPES:
            return await _dispatch_info(raw_type, payload, sync)

        # ── UNKNOWN ──────────────────────────────────────────────────
        # Forward as a raw info log so it surfaces in the audit panel
        # for triage. The dashboard's `is_system` classifier keeps it
        # muted instead of flagging it as "unmapped" yellow.
        log.info(
            "vrchat_audit: forwarding unknown event_type=%s as info row "
            "(add it to one of the maps in vrchat_audit_dispatcher.py)",
            raw_type,
        )
        return await _dispatch_info(raw_type, payload, sync)

    except Exception:
        log.exception("vrchat_audit dispatch failed (event_type=%s)", raw_type)
        return None


# ─── Internal helpers ────────────────────────────────────────────────────


async def _dispatch_moderation(action_type: str, payload: dict, sync) -> Optional[dict]:
    """Forward a moderation row to /sync/log. Preserves the raw VRChat
    action_type so the dashboard's normalizer maps it (e.g.
    `group.instance.warn` → warn bucket)."""
    payload = _ensure_actor_as_staff(payload)
    res = await sync.push_log(
        action_type=action_type,
        category="moderation",
        **payload,
    )
    _log_result("moderation", action_type, res)
    return res


async def _dispatch_lifecycle(
    event_type: str, raw_type: str, payload: dict, sync,
) -> Optional[dict]:
    """Forward an instance/join-request lifecycle row via the typed
    /sync/vrchat-activity endpoint. Falls back to /sync/log if the
    typed helper isn't available on this dashboard_sync version."""
    if hasattr(sync, "push_vrchat_activity"):
        # The typed endpoint already does its own actor → staff_name
        # resolution server-side, so we don't need to inject it here.
        res = await sync.push_vrchat_activity(event_type=event_type, **payload)
        _log_result("lifecycle", f"{raw_type}→{event_type}", res)
        return res
    # Fallback for older dashboard_sync builds without push_vrchat_activity:
    # forward as a raw info log. The dashboard still classifies it
    # correctly via _SYSTEM_ACTION_TYPES, but `/sync/log` defaults
    # `staff_name` to "System" if we don't supply one — so promote
    # the actor name into `staff_name` first.
    return await _dispatch_info(raw_type, payload, sync)


async def _dispatch_info(action_type: str, payload: dict, sync) -> Optional[dict]:
    """Forward an info-class row to /sync/log with category="info"."""
    payload = _ensure_actor_as_staff(payload)
    res = await sync.push_log(
        action_type=action_type,
        category="info",
        **payload,
    )
    _log_result("info", action_type, res)
    return res


def _ensure_actor_as_staff(payload: dict) -> dict:
    """Promote the row's VRChat actor into `staff_name` / `staff_discord_id`
    when those fields are missing.

    Why this matters: `/sync/log`'s pydantic model defaults `staff_name`
    to the literal string "System" when no value is supplied. Without
    this promotion every audit-log row would render as
    "System performed Instance Opened" on the dashboard's Logs page,
    losing the actor identity that VRChat handed us. The dashboard's
    typed `/sync/vrchat-activity` endpoint already does this server-side,
    so we only need this helper for moderation + info rows that flow
    through `/sync/log`.

    Mutation rules — never overwrite a value the caller explicitly set:
      • If `staff_name` already present and non-empty, leave alone.
      • Else prefer the Discord identity (`actor_discord_name`/`_id`)
        over the VRChat identity (richer in the Logs page since the
        Discord avatar resolver kicks in).
      • Fall back to the VRChat name + id otherwise.
    """
    out = dict(payload)
    if not (out.get("staff_name") or "").strip():
        candidate = (
            out.get("actor_discord_name")
            or out.get("actor_vrchat_name")
            or ""
        ).strip()
        if candidate:
            out["staff_name"] = candidate
    if not (out.get("staff_discord_id") or "").strip():
        candidate_id = (
            out.get("actor_discord_id")
            or out.get("actor_vrchat_id")
            or ""
        ).strip()
        if candidate_id:
            out["staff_discord_id"] = candidate_id
    return out


def _log_result(channel: str, label: str, res) -> None:
    if isinstance(res, dict) and res.get("success"):
        log.debug(
            "vrchat_audit dispatched (channel=%s event=%s log_id=%s)",
            channel, label, res.get("log_id"),
        )
    else:
        log.warning(
            "vrchat_audit dispatch returned non-success (channel=%s event=%s response=%s)",
            channel, label, res,
        )
