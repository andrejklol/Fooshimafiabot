import asyncio
import json
import logging
from http.cookies import SimpleCookie
from urllib.parse import quote

import websockets

from core.cache import app_state
from core.utils import send_error_log, vrchat_cooldown_active

from .status_pipeline import process_user_status
from .vrchat_auth import (
    _ensure_pipeline_state,
    _ensure_recent_activity_state,
    _run_vrc_api_call,
)

from .vrchat_client import (
    _USER_AGENT,
    _normalize_status_value,
    _normalize_vrc_name,
    _normalize_vrc_user_id,
    _now_ts,
)

from .vrchat_group import (
    _member_cache_is_stale,
    ensure_vrc_group_cache_ready,
    refresh_vrc_group_members,
)

log = logging.getLogger("vrchat_presence")

AMBIGUOUS_STATUS_RECHECK_SECONDS = 6

PIPELINE_URL_BASE = "wss://pipeline.vrchat.cloud/"
PIPELINE_RECONNECT_SECONDS = 5
PIPELINE_STALE_SECONDS = 180

RECENT_ACTIVITY_WINDOW_SECONDS = 20 * 60
FRIEND_PRESENCE_CACHE_SECONDS = 90


# ============================================================
# PIPELINE CACHE
# ============================================================

def _pipeline_cache_is_stale() -> bool:
    last = float(getattr(app_state, "vrc_pipeline_last_event_ts", 0.0) or 0.0)
    return last <= 0 or (_now_ts() - last) >= PIPELINE_STALE_SECONDS


# ============================================================
# COOKIE HELPERS
# ============================================================

def _extract_auth_cookie_from_client() -> str | None:

    client = getattr(app_state, "vrc_client", None)
    if not client:
        return None

    rest_client = getattr(client, "rest_client", None)
    pool_manager = getattr(rest_client, "pool_manager", None) if rest_client else None

    sources = [
        getattr(rest_client, "cookies", None),
        getattr(rest_client, "cookie_jar", None),
        getattr(pool_manager, "cookie_jar", None) if pool_manager else None,
        getattr(pool_manager, "headers", None) if pool_manager else None,
        getattr(client, "cookies", None),
        getattr(client, "cookie_jar", None),
        getattr(client, "default_headers", None),
    ]

    for source in sources:
        if not source:
            continue

        try:
            for cookie in source:
                name = str(getattr(cookie, "name", "")).lower()
                value = str(getattr(cookie, "value", ""))

                if name in {"auth", "authtoken"} and value:
                    return value
        except Exception:
            pass

        try:
            if hasattr(source, "items"):
                for k, v in source.items():
                    if str(k).lower() in {"auth", "authtoken"}:
                        return str(v)

                    if str(k).lower() == "cookie":
                        parsed = SimpleCookie()
                        parsed.load(v)

                        for cname in ("auth", "authtoken"):
                            if parsed.get(cname):
                                return parsed[cname].value
        except Exception:
            pass

    return None


def _build_pipeline_url() -> str | None:

    cookie = _extract_auth_cookie_from_client()

    if not cookie:
        return None

    return f"{PIPELINE_URL_BASE}?authToken={quote(cookie, safe='')}"


def _build_pipeline_headers():

    cookie = _extract_auth_cookie_from_client()

    headers = {
        "User-Agent": _USER_AGENT,
        "Origin": "https://vrchat.com",
    }

    if cookie:
        c = SimpleCookie()
        c["auth"] = cookie
        headers["Cookie"] = c.output(header="").strip()

    return headers


# ============================================================
# RECENT ACTIVITY
# ============================================================

def mark_vrc_user_recently_active(user_id: str):

    _ensure_recent_activity_state()

    uid = str(user_id or "").strip()

    if uid:
        app_state.vrc_recent_activity[uid] = _now_ts()


def _recent_activity(user_id: str) -> bool:

    _ensure_recent_activity_state()

    uid = str(user_id or "").strip()

    ts = float(app_state.vrc_recent_activity.get(uid, 0) or 0)

    return ts > 0 and (_now_ts() - ts) <= RECENT_ACTIVITY_WINDOW_SECONDS


# ============================================================
# PIPELINE PRESENCE CACHE
# ============================================================

def _set_pipeline_presence(
    user_id: str,
    online: bool,
    location: str | None,
    platform: str | None,
):

    _ensure_pipeline_state()

    uid = str(user_id or "").strip()

    old = app_state.vrc_pipeline_friend_presence.get(uid, {})

    new = {
        "online": bool(online),
        "location": str(location or "").lower(),
        "platform": str(platform or "").lower(),
        "updated_at": _now_ts(),
    }

    changed = old.get("online") != new["online"]

    app_state.vrc_pipeline_friend_presence[uid] = {
        **old,
        **new,
    }

    app_state.vrc_pipeline_last_event_ts = _now_ts()

    return changed


def _get_pipeline_presence(user_id: str):

    _ensure_pipeline_state()

    uid = str(user_id or "").strip()

    entry = app_state.vrc_pipeline_friend_presence.get(uid)

    if not entry:
        return None

    ts = float(entry.get("updated_at", 0) or 0)

    if (_now_ts() - ts) >= PIPELINE_STALE_SECONDS:
        return None

    return entry


# ============================================================
# PIPELINE EVENTS
# ============================================================

def _decode_pipeline_message(raw):

    try:
        payload = json.loads(raw)
    except Exception:
        return None

    content = payload.get("content")

    if isinstance(content, str):

        try:
            payload["content"] = json.loads(content)
        except Exception:
            pass

    return payload


def _schedule_status_update(user_id, ws_online, friend_presence, status, platform):

    asyncio.create_task(

        process_user_status(
            user_id=user_id,
            ws_online=ws_online,
            friend_presence=friend_presence,
            user_status=status,
            last_platform=platform,
        )

    )


def _handle_pipeline_event(payload):

    _ensure_pipeline_state()

    content = payload.get("content") or {}

    user_id = str(
        content.get("userId")
        or content.get("user_id")
        or ""
    ).strip()

    if not user_id:
        return

    location = str(content.get("location") or "").lower()

    platform = str(content.get("platform") or "").lower()

    event_type = str(payload.get("type") or "").lower()

    if event_type == "friend-online":

        _set_pipeline_presence(user_id, True, location, platform)

        _schedule_status_update(
            user_id,
            True,
            True,
            "active",
            platform,
        )

    elif event_type == "friend-offline":

        _set_pipeline_presence(user_id, False, location, platform)

        _schedule_status_update(
            user_id,
            False,
            False,
            "offline",
            platform,
        )

    elif event_type == "friend-location":

        online = location not in {"offline", ""}

        _set_pipeline_presence(user_id, online, location, platform)

        _schedule_status_update(
            user_id,
            online,
            online,
            "active" if online else "offline",
            platform,
        )


# ============================================================
# PIPELINE LOOP
# ============================================================

async def _pipeline_loop():

    _ensure_pipeline_state()

    while True:

        if vrchat_cooldown_active():

            await asyncio.sleep(PIPELINE_RECONNECT_SECONDS)

            continue

        url = _build_pipeline_url()

        if not url:

            await asyncio.sleep(PIPELINE_RECONNECT_SECONDS)

            continue

        try:

            async with websockets.connect(
                url,
                additional_headers=_build_pipeline_headers(),
                ping_interval=20,
                ping_timeout=20,
            ) as ws:

                app_state.vrc_pipeline_ws = ws

                log.info("pipeline connected")

                async for raw in ws:

                    payload = _decode_pipeline_message(raw)

                    if payload:
                        _handle_pipeline_event(payload)

        except Exception as e:

            log.warning("pipeline reconnect: %s", e)

            await asyncio.sleep(PIPELINE_RECONNECT_SECONDS)


def ensure_pipeline_listener_started():

    _ensure_pipeline_state()

    task = getattr(app_state, "vrc_pipeline_task", None)

    if not task or task.done():

        app_state.vrc_pipeline_task = asyncio.create_task(
            _pipeline_loop()
        )


async def stop_pipeline_listener():

    task = getattr(app_state, "vrc_pipeline_task", None)

    if task and not task.done():

        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass


# ============================================================
# USER STATUS
# ============================================================

async def resolve_vrchat_user_id(vrchat_username=None, vrchat_user_id=None):

    uid = _normalize_vrc_user_id(vrchat_user_id)

    if uid:
        return uid

    wanted = _normalize_vrc_name(vrchat_username)

    if not wanted:
        return None

    if not app_state.vrc_group_member_roles:

        await ensure_vrc_group_cache_ready()

    for uid in app_state.vrc_group_member_roles:

        if _normalize_vrc_name(
            app_state.target_name_cache.get(uid)
        ) == wanted:

            return uid

    if _member_cache_is_stale():

        await refresh_vrc_group_members(True)

        return await resolve_vrchat_user_id(
            vrchat_username,
            vrchat_user_id,
        )

    return None


async def get_vrchat_user_status(vrchat_username=None, vrchat_user_id=None):

    uid = await resolve_vrchat_user_id(
        vrchat_username,
        vrchat_user_id,
    )

    if not uid:
        return False, None, None

    entry = _get_pipeline_presence(uid)

    if entry and not _pipeline_cache_is_stale():

        return entry["online"], uid, "pipeline"

    try:

        user = await _run_vrc_api_call(
            app_state.vrc_users_api.get_user,
            uid,
        )

        status = _normalize_status_value(user.status)

        await process_user_status(
            user_id=uid,
            user_status=status,
            mod_action_recent=_recent_activity(uid),
        )

        return status != "offline", uid, status

    except Exception as e:

        await send_error_log(
            "VRChat status error",
            e,
        )

        return False, uid, None


async def is_vrchat_user_online(vrchat_username=None, vrchat_user_id=None):

    online, _, _ = await get_vrchat_user_status(
        vrchat_username,
        vrchat_user_id,
    )

    return online


__all__ = [

    "ensure_pipeline_listener_started",

    "get_vrchat_user_status",

    "is_vrchat_user_online",

    "mark_vrc_user_recently_active",

    "resolve_vrchat_user_id",

    "stop_pipeline_listener",
]
