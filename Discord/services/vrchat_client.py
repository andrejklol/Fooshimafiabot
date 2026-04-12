import asyncio
import json
import logging
import os
import time
from http.cookies import SimpleCookie
from urllib.parse import quote

import urllib3
import websockets
from vrchatapi import ApiClient, Configuration
from vrchatapi.api.authentication_api import AuthenticationApi
from vrchatapi.models.two_factor_email_code import TwoFactorEmailCode

from core.cache import app_state
from core.config import (
    GROUP_ID,
    VRC_STAFF_ROLE_NAMES,
    VRCHAT_EMAIL_OTP,
    VRCHAT_PASSWORD,
    VRCHAT_USERNAME,
)
from core.utils import (
    format_remaining_cooldown,
    run_blocking,
    send_error_log,
    vrchat_cooldown_active,
)
from services.status_pipeline import process_user_status

log = logging.getLogger("vrchat_client")

_USER_AGENT = "FooshiMafiaBot/1.3 (contact: fooshimafia@gmail.com)"


# ============================================================
# CONSTANTS
# ============================================================

GROUP_ROLE_REFRESH_TTL_SECONDS = 900
GROUP_MEMBER_REFRESH_TTL_SECONDS = 900
GROUP_INFO_REFRESH_TTL_SECONDS = 900
AMBIGUOUS_STATUS_RECHECK_SECONDS = 6

PIPELINE_URL_BASE = "wss://pipeline.vrchat.cloud/"
PIPELINE_RECONNECT_SECONDS = 5
PIPELINE_STALE_SECONDS = 180

RECENT_ACTIVITY_WINDOW_SECONDS = 20 * 60
FRIEND_PRESENCE_CACHE_SECONDS = 90

VRCHAT_API_RETRIES = 3
VRCHAT_API_BASE_DELAY_SECONDS = 2.0
GROUP_MEMBERS_PAGE_DELAY_SECONDS = 0.5


# ============================================================
# BASIC HELPERS
# ============================================================

def _now_ts() -> float:
    return time.time()


def _normalize_vrc_name(value: str | None) -> str:
    return (value or "").strip().casefold()


def _normalize_vrc_user_id(value: str | None) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None


def _normalize_status_value(value) -> str:
    return str(value or "").strip().lower()


def _extract_role_name(role_obj) -> str | None:
    for field in ("name", "display_name", "role_name"):
        if (value := str(getattr(role_obj, field, None) or "").strip()):
            return value
    return None


def _extract_role_id(role_obj) -> str | None:
    for field in ("id", "role_id"):
        if (value := str(getattr(role_obj, field, None) or "").strip()):
            return value
    return None


def _member_role_names_from_obj(member_obj) -> list[str]:
    role_names: list[str] = []

    for field in ("roles", "role_names"):
        for value in getattr(member_obj, field, None) or []:
            if hasattr(value, "__dict__"):
                if name := _extract_role_name(value):
                    role_names.append(name.lower())
            elif s := str(value).strip().lower():
                role_names.append(s)

    for role_id in getattr(member_obj, "role_ids", None) or []:
        if role_name := app_state.vrc_group_role_map.get(str(role_id)):
            role_names.append(role_name.lower())

    return list(set(role_names))


def _member_role_ids_from_obj(member_obj) -> list[str]:
    role_ids: list[str] = []

    for field in ("role_ids", "roleIds"):
        for value in getattr(member_obj, field, None) or []:
            if cleaned := str(value or "").strip():
                role_ids.append(cleaned)

    for field in ("roles", "role_names"):
        for value in getattr(member_obj, field, None) or []:
            if hasattr(value, "__dict__"):
                if role_id := _extract_role_id(value):
                    role_ids.append(role_id)

    return list(set(role_ids))


def _extract_member_display_name(member_obj) -> str | None:
    for field in (
        "display_name",
        "displayName",
        "user_display_name",
        "userDisplayName",
        "username",
        "user_name",
        "name",
    ):
        if value := str(getattr(member_obj, field, None) or "").strip():
            return value
    return None


def _extract_group_name(group_obj) -> str | None:
    for field in ("name", "display_name", "displayName", "group_name"):
        if (value := str(getattr(group_obj, field, None) or "").strip()):
            return value
    return None


def _extract_group_id(group_obj) -> str | None:
    for field in ("id", "group_id", "groupId"):
        if (value := str(getattr(group_obj, field, None) or "").strip()):
            return value
    return None


def _fallback_user_name(user_id: str) -> str:
    return f"User {str(user_id).replace('usr_', '')[:8]}"


# ============================================================
# CACHE STALENESS
# ============================================================

def _role_cache_is_stale() -> bool:
    return (
        _now_ts() - (getattr(app_state, "vrc_group_roles_last_refresh", 0.0) or 0.0)
    ) >= GROUP_ROLE_REFRESH_TTL_SECONDS


def _member_cache_is_stale() -> bool:
    return (
        _now_ts() - (getattr(app_state, "vrc_group_members_last_refresh", 0.0) or 0.0)
    ) >= GROUP_MEMBER_REFRESH_TTL_SECONDS


def _group_info_cache_is_stale() -> bool:
    return (
        _now_ts() - (getattr(app_state, "vrc_group_info_last_refresh", 0.0) or 0.0)
    ) >= GROUP_INFO_REFRESH_TTL_SECONDS


def _pipeline_cache_is_stale() -> bool:
    last = getattr(app_state, "vrc_pipeline_last_event_ts", 0.0) or 0.0
    return last <= 0 or (_now_ts() - last) >= PIPELINE_STALE_SECONDS


# ============================================================
# APP STATE SETUP
# ============================================================

def _ensure_attr_default(attr: str, default) -> None:
    if not hasattr(app_state, attr):
        setattr(app_state, attr, default() if callable(default) else default)


def _ensure_pipeline_state() -> None:
    _ensure_attr_default("vrc_pipeline_friend_presence", dict)
    _ensure_attr_default("vrc_pipeline_ws", None)
    _ensure_attr_default("vrc_pipeline_task", None)
    _ensure_attr_default("vrc_pipeline_last_event_ts", 0.0)
    _ensure_attr_default("vrc_pipeline_connected", False)


def _ensure_recent_activity_state() -> None:
    _ensure_attr_default("vrc_recent_activity", dict)
    _ensure_attr_default("vrc_friend_presence_cache", dict)
    _ensure_attr_default("vrc_friend_presence_last_refresh", 0.0)


def _ensure_vrc_sync_state() -> None:
    _ensure_attr_default("vrc_group_roles_last_error_ts", 0.0)
    _ensure_attr_default("vrc_group_members_last_error_ts", 0.0)
    _ensure_attr_default("vrc_group_info_last_error_ts", 0.0)
    _ensure_attr_default("vrc_group_roles_refresh_lock", asyncio.Lock)
    _ensure_attr_default("vrc_group_members_refresh_lock", asyncio.Lock)
    _ensure_attr_default("vrc_group_info_refresh_lock", asyncio.Lock)
    _ensure_attr_default("group_cache", dict)
    _ensure_attr_default("vrc_group_info_last_refresh", 0.0)
    _ensure_attr_default("vrc_group_member_role_ids", dict)


# ============================================================
# ERROR / RETRY HELPERS
# ============================================================

def _is_connection_reset_error(exc: Exception) -> bool:
    text = str(exc or "")
    return (
        isinstance(exc, ConnectionResetError)
        or "ConnectionResetError" in text
        or "Connection aborted" in text
        or "forcibly closed by the remote host" in text
        or "Max retries exceeded" in text
        or "ProtocolError" in text
    )


async def _run_vrc_api_call(
    func,
    *args,
    retries: int = VRCHAT_API_RETRIES,
    base_delay: float = VRCHAT_API_BASE_DELAY_SECONDS,
    **kwargs,
):
    last_exc = None

    for attempt in range(1, retries + 1):
        try:
            return await run_blocking(func, *args, **kwargs)

        except Exception as exc:
            last_exc = exc

            if attempt >= retries:
                raise

            delay = base_delay * attempt
            func_name = getattr(func, "__name__", str(func))

            log.warning(
                "VRChat API retry %s/%s func=%s type=%s error=%s delay=%.1fs",
                attempt,
                retries,
                func_name,
                type(exc).__name__,
                exc,
                delay,
            )

            if attempt == 1 and _is_connection_reset_error(exc):
                await send_error_log(
                    "VRChat API Retry Warning",
                    exc,
                    severity="warning",
                    trace_id="vrchat_retry",
                    extra={
                        "attempt": f"{attempt}/{retries}",
                        "function": func_name,
                        "delay_seconds": f"{delay:.1f}",
                        "error_type": type(exc).__name__,
                    },
                )

            await asyncio.sleep(delay)

    raise last_exc


async def _send_rate_limited_error(
    title: str,
    exc: Exception,
    attr_name: str,
    cooldown_seconds: int = 300,
) -> None:
    _ensure_vrc_sync_state()

    now = _now_ts()
    last_sent = float(getattr(app_state, attr_name, 0.0) or 0.0)

    log.warning("%s: %s: %s", title, type(exc).__name__, exc)

    if (now - last_sent) < cooldown_seconds and _is_connection_reset_error(exc):
        return

    setattr(app_state, attr_name, now)

    await send_error_log(
        title,
        exc,
        severity="error",
        trace_id="vrchat_cache",
        extra={
            "error_type": type(exc).__name__,
        },
    )


# ============================================================
# RECENT ACTIVITY SIGNAL
# ============================================================

def mark_vrc_user_recently_active(user_id: str) -> None:
    _ensure_recent_activity_state()

    if user_id := str(user_id or "").strip():
        app_state.vrc_recent_activity[user_id] = _now_ts()
        log.debug("recent_activity marked user_id=%s", user_id)


def _is_vrc_user_recently_active(user_id: str) -> bool:
    _ensure_recent_activity_state()

    user_id = str(user_id or "").strip()
    if not user_id:
        return False

    ts = float(app_state.vrc_recent_activity.get(user_id, 0.0) or 0.0)
    is_recent = ts > 0 and (_now_ts() - ts) <= RECENT_ACTIVITY_WINDOW_SECONDS

    log.debug(
        "recent_activity check user_id=%s last_seen=%s recent=%s",
        user_id,
        ts,
        is_recent,
    )

    return is_recent


# ============================================================
# PIPELINE PRESENCE CACHE
# ============================================================

def _set_pipeline_presence(
    user_id: str,
    *,
    online: bool,
    source_event: str,
    location: str | None = None,
    platform: str | None = None,
) -> bool:
    _ensure_pipeline_state()

    user_id = str(user_id or "").strip()
    if not user_id:
        return False

    old = app_state.vrc_pipeline_friend_presence.get(user_id, {})
    new_location = str(location or "").strip().lower()
    new_platform = str(platform or "").strip().lower()

    changed = (
        old.get("online") != bool(online)
        or old.get("location") != new_location
        or old.get("platform") != new_platform
        or old.get("source_event") != source_event
    )

    app_state.vrc_pipeline_friend_presence[user_id] = {
        **old,
        "online": bool(online),
        "source_event": source_event,
        "location": new_location,
        "platform": new_platform,
        "updated_at": _now_ts(),
    }
    app_state.vrc_pipeline_last_event_ts = _now_ts()

    return changed


def _get_pipeline_presence(user_id: str) -> dict | None:
    _ensure_pipeline_state()

    user_id = str(user_id or "").strip()
    if not user_id:
        return None

    entry = app_state.vrc_pipeline_friend_presence.get(user_id)
    if not entry:
        return None

    updated_at = float(entry.get("updated_at", 0.0) or 0.0)
    return None if updated_at <= 0 or (_now_ts() - updated_at) >= PIPELINE_STALE_SECONDS else entry


# ============================================================
# COOKIE EXTRACTION / PIPELINE AUTH
# ============================================================

def _extract_auth_cookie_from_client() -> str | None:
    client = getattr(app_state, "vrc_client", None)
    if client is None:
        log.debug("cookie extraction skipped: no vrc_client")
        return None

    rest_client = getattr(client, "rest_client", None)
    pool_manager = getattr(rest_client, "pool_manager", None) if rest_client else None

    sources: list[tuple[str, object]] = [
        *(
            [
                ("rest_client.cookies", getattr(rest_client, "cookies", None)),
                ("rest_client.cookie_jar", getattr(rest_client, "cookie_jar", None)),
            ]
            if rest_client
            else []
        ),
        *(
            [
                ("pool_manager.cookie_jar", getattr(pool_manager, "cookie_jar", None)),
                ("pool_manager.cookies", getattr(pool_manager, "cookies", None)),
                ("pool_manager.headers", getattr(pool_manager, "headers", None)),
            ]
            if pool_manager
            else []
        ),
        ("client.cookies", getattr(client, "cookies", None)),
        ("client.cookie_jar", getattr(client, "cookie_jar", None)),
        ("client.default_headers", getattr(client, "default_headers", None)),
    ]

    for source_name, source in sources:
        if source is None:
            continue

        try:
            for cookie in source:
                name = str(getattr(cookie, "name", "") or "").strip().lower()
                value = str(getattr(cookie, "value", "") or "").strip()
                if name in {"auth", "authtoken"} and value:
                    log.debug("found auth cookie in %s", source_name)
                    return value
        except Exception:
            pass

        try:
            if hasattr(source, "items"):
                for name, value in source.items():
                    n = str(name or "").strip().lower()
                    v = str(value or "").strip()

                    if n in {"auth", "authtoken"} and v:
                        log.debug("found auth cookie in %s", source_name)
                        return v

                    if n == "cookie" and v:
                        parsed = SimpleCookie()
                        parsed.load(v)
                        for cookie_name in ("auth", "authtoken"):
                            if (morsel := parsed.get(cookie_name)) and morsel.value:
                                log.debug(
                                    "found auth cookie inside Cookie header from %s",
                                    source_name,
                                )
                                return morsel.value
        except Exception:
            pass

        try:
            raw = str(source).strip()
            if raw and ("auth=" in raw or "authtoken=" in raw):
                parsed = SimpleCookie()
                parsed.load(raw)
                for cookie_name in ("auth", "authtoken"):
                    if (morsel := parsed.get(cookie_name)) and morsel.value:
                        log.debug(
                            "found auth cookie inside raw string from %s",
                            source_name,
                        )
                        return morsel.value
        except Exception:
            pass

    log.debug("auth cookie not found in known client sources")
    return None


def _build_pipeline_headers() -> dict[str, str]:
    headers = {
        "User-Agent": _USER_AGENT,
        "Origin": "https://vrchat.com",
    }

    if auth_cookie := _extract_auth_cookie_from_client():
        cookie = SimpleCookie()
        cookie["auth"] = auth_cookie
        headers["Cookie"] = cookie.output(header="").strip()

    return headers


def _build_pipeline_url() -> str | None:
    if auth_cookie := _extract_auth_cookie_from_client():
        return f"{PIPELINE_URL_BASE}?authToken={quote(auth_cookie, safe='')}"
    return None


# ============================================================
# PIPELINE MESSAGE HANDLING
# ============================================================

def _decode_pipeline_message(raw_message: str) -> dict | None:
    try:
        payload = json.loads(raw_message)
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None

    if isinstance(content := payload.get("content"), str) and content.strip():
        try:
            payload["content"] = json.loads(content.strip())
        except Exception:
            pass

    return payload


def _handle_pipeline_event(payload: dict) -> None:
    _ensure_pipeline_state()

    event_type = str(payload.get("type", "") or "").strip().lower()
    content = payload.get("content")
    app_state.vrc_pipeline_last_event_ts = _now_ts()

    if not isinstance(content, dict):
        return

    user_id = str(
        content.get("userId") or content.get("userid") or content.get("user_id") or ""
    ).strip()
    if not user_id:
        return

    if isinstance(user_obj := content.get("user"), dict):
        display_name = str(
            user_obj.get("displayName")
            or user_obj.get("display_name")
            or user_obj.get("username")
            or ""
        ).strip()
        if display_name:
            app_state.target_name_cache[user_id] = display_name

    location = str(content.get("location", "") or "").strip().lower()
    platform = str(content.get("platform", "") or "").strip().lower()

    offline_locations = {"", "offline", "offline:offline"}

    if event_type == "friend-online":
        changed = _set_pipeline_presence(
            user_id,
            online=True,
            source_event=event_type,
            location=location,
            platform=platform,
        )
        asyncio.create_task(
            process_user_status(
                user_id=user_id,
                ws_online=True,
                friend_presence=True,
                user_status="active",
                last_platform=platform,
            )
        )
        if changed:
            log.info(
                "pipeline friend-online user_id=%s location=%r platform=%r",
                user_id,
                location,
                platform,
            )

    elif event_type == "friend-location":
        online = location not in offline_locations
        changed = _set_pipeline_presence(
            user_id,
            online=online,
            source_event=event_type,
            location=location,
            platform=platform,
        )
        asyncio.create_task(
            process_user_status(
                user_id=user_id,
                ws_online=online,
                friend_presence=online,
                user_status="active" if online else "offline",
                last_platform=platform,
            )
        )
        if changed:
            log.info(
                "pipeline friend-location user_id=%s location=%r online=%s",
                user_id,
                location,
                online,
            )

    elif event_type == "friend-active":
        effective_platform = platform or "web"

        changed = _set_pipeline_presence(
            user_id,
            online=True,
            source_event=event_type,
            location=location,
            platform=effective_platform,
        )

        asyncio.create_task(
            process_user_status(
                user_id=user_id,
                ws_online=None,
                friend_presence=True,
                user_status="active",
                last_platform=effective_platform,
            )
        )

        if changed:
            log.info(
                "pipeline friend-active user_id=%s platform=%r",
                user_id,
                effective_platform,
            )

    elif event_type == "friend-offline":
        changed = _set_pipeline_presence(
            user_id,
            online=False,
            source_event=event_type,
            location="offline",
            platform=platform,
        )
        asyncio.create_task(
            process_user_status(
                user_id=user_id,
                ws_online=False,
                friend_presence=False,
                user_status="offline",
                last_platform=platform,
            )
        )
        if changed:
            log.info("pipeline friend-offline user_id=%s", user_id)

    else:
        log.debug("pipeline ignored event_type=%s user_id=%s", event_type, user_id)


async def _pipeline_receiver_loop() -> None:
    _ensure_pipeline_state()

    while True:
        if vrchat_cooldown_active() or not getattr(app_state, "vrc_auth_api", None):
            app_state.vrc_pipeline_connected = False
            app_state.vrc_pipeline_ws = None
            await asyncio.sleep(PIPELINE_RECONNECT_SECONDS)
            continue

        url = _build_pipeline_url()
        if not url:
            app_state.vrc_pipeline_connected = False
            app_state.vrc_pipeline_ws = None
            log.warning("pipeline auth cookie missing; cannot connect websocket")
            await asyncio.sleep(PIPELINE_RECONNECT_SECONDS)
            continue

        try:
            log.info("pipeline connecting...")

            async with websockets.connect(
                url,
                additional_headers=_build_pipeline_headers(),
                ping_interval=20,
                ping_timeout=20,
                max_size=2**20,
            ) as ws:
                app_state.vrc_pipeline_ws = ws
                app_state.vrc_pipeline_connected = True
                app_state.vrc_pipeline_last_event_ts = _now_ts()

                log.info("pipeline connected")

                async for raw_message in ws:
                    if payload := _decode_pipeline_message(raw_message):
                        _handle_pipeline_event(payload)

        except asyncio.CancelledError:
            app_state.vrc_pipeline_connected = False
            app_state.vrc_pipeline_ws = None
            log.info("pipeline cancelled")
            raise

        except Exception as exc:
            app_state.vrc_pipeline_connected = False
            app_state.vrc_pipeline_ws = None
            log.warning(
                "pipeline connection error: %s: %s",
                type(exc).__name__,
                exc,
            )
            await asyncio.sleep(PIPELINE_RECONNECT_SECONDS)


def ensure_pipeline_listener_started() -> None:
    _ensure_pipeline_state()

    task = getattr(app_state, "vrc_pipeline_task", None)
    if task is None or task.done():
        app_state.vrc_pipeline_task = asyncio.create_task(_pipeline_receiver_loop())
        app_state.vrc_pipeline_connected = False
        log.info("pipeline listener task started")


async def stop_pipeline_listener() -> None:
    _ensure_pipeline_state()

    task = getattr(app_state, "vrc_pipeline_task", None)
    if task is None:
        return

    if not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    app_state.vrc_pipeline_task = None
    app_state.vrc_pipeline_connected = False
    app_state.vrc_pipeline_ws = None

    log.info("pipeline listener task stopped")


# ============================================================
# FRIEND PRESENCE CACHE
# ============================================================

async def _refresh_friend_presence_cache(force: bool = False) -> dict[str, bool]:
    _ensure_recent_activity_state()

    last = float(getattr(app_state, "vrc_friend_presence_last_refresh", 0.0) or 0.0)
    if not force and (_now_ts() - last) < FRIEND_PRESENCE_CACHE_SECONDS:
        return app_state.vrc_friend_presence_cache

    if not getattr(app_state, "vrc_auth_api", None):
        return {}

    try:
        me = await _run_vrc_api_call(app_state.vrc_auth_api.get_current_user)

        offline = getattr(me, "offline_friends", None) or getattr(me, "offlineFriends", None) or []
        online = getattr(me, "online_friends", None) or getattr(me, "onlineFriends", None) or []
        active = getattr(me, "active_friends", None) or getattr(me, "activeFriends", None) or []

        cache: dict[str, bool] = {}

        for uid in offline:
            if uid := str(uid or "").strip():
                cache[uid] = False

        for uid in (*online, *active):
            if uid := str(uid or "").strip():
                cache[uid] = True

        app_state.vrc_friend_presence_cache = cache
        app_state.vrc_friend_presence_last_refresh = _now_ts()

        log.debug(
            "friend_presence refreshed count=%s active=%s online=%s offline=%s",
            len(cache),
            len(active),
            len(online),
            len(offline),
        )

        return cache

    except Exception as exc:
        log.warning("friend_presence refresh error: %s: %s", type(exc).__name__, exc)
        return app_state.vrc_friend_presence_cache


async def _get_friend_presence_online(user_id: str) -> bool | None:
    cache = await _refresh_friend_presence_cache(force=False)
    user_id = str(user_id or "").strip()

    if not user_id or user_id not in cache:
        return None

    is_online = bool(cache[user_id])
    log.debug("friend_presence user_id=%s is_online=%s", user_id, is_online)
    return is_online


# ============================================================
# ROLE / STAFF HELPERS
# ============================================================

def get_cached_vrc_user_roles(user_id: str) -> list[str]:
    user_id = str(user_id or "").strip()
    return app_state.vrc_group_member_roles.get(user_id, []) if user_id else []


def get_cached_vrc_user_role_ids(user_id: str) -> list[str]:
    user_id = str(user_id or "").strip()
    return app_state.vrc_group_member_role_ids.get(user_id, []) if user_id else []


def is_cached_vrc_user_staff(user_id: str) -> bool:
    wanted = {str(x).strip().casefold() for x in VRC_STAFF_ROLE_NAMES}
    staff_role_ids = {str(x).strip() for x in getattr(app_state, "vrchat_staff_role_ids", set())}

    role_name_match = any(
        str(r).strip().casefold() in wanted
        for r in get_cached_vrc_user_roles(user_id)
    )

    role_id_match = any(
        str(rid).strip() in staff_role_ids
        for rid in get_cached_vrc_user_role_ids(user_id)
    )

    return role_name_match or role_id_match


async def vrc_user_is_staff(user_id: str) -> bool:
    user_id = str(user_id or "").strip()
    if not user_id:
        return False

    if is_cached_vrc_user_staff(user_id):
        return True

    if not app_state.vrc_group_member_roles:
        await ensure_vrc_group_cache_ready()

    if is_cached_vrc_user_staff(user_id):
        return True

    if _member_cache_is_stale():
        await refresh_vrc_group_members(force=True)

    return is_cached_vrc_user_staff(user_id)


async def get_pretty_vrc_name(entry) -> tuple[str, str]:
    target_id = str(getattr(entry, "target_id", "") or "").strip()
    if not target_id:
        return "Unknown User", "N/A"

    for field in ("target_display_name", "target_username", "target_name", "display_name"):
        if (value := str(getattr(entry, field, None) or "").strip()):
            app_state.target_name_cache[target_id] = value
            return value, target_id

    if cached := app_state.target_name_cache.get(target_id):
        return cached, target_id

    short_id = target_id.replace("usr_", "")[:8]
    return f"User {short_id}", target_id


# ============================================================
# GROUP CACHE
# ============================================================

async def refresh_group_cache_once(force: bool = False) -> None:
    if not app_state.vrc_groups_api or vrchat_cooldown_active():
        return

    _ensure_vrc_sync_state()

    async with app_state.vrc_group_info_refresh_lock:
        if not force and app_state.group_cache and not _group_info_cache_is_stale():
            return

        try:
            group = await _run_vrc_api_call(
                app_state.vrc_groups_api.get_group,
                GROUP_ID,
            )

            if group is None:
                log.warning("group_info refresh returned no group object; keeping existing cache")
                return

            group_id = _extract_group_id(group) or str(GROUP_ID)
            group_name = _extract_group_name(group)

            if not group_name:
                log.warning("group_info refresh returned no group name; keeping existing cache")
                return

            app_state.group_cache[group_id] = {
                "id": group_id,
                "name": group_name,
                "displayName": group_name,
            }
            app_state.vrc_group_info_last_refresh = _now_ts()

            if hasattr(app_state, "sync_cache_aliases"):
                app_state.sync_cache_aliases()

            log.info("cached group info id=%s name=%s", group_id, group_name)

        except Exception as exc:
            await _send_rate_limited_error(
                "Group Cache Error",
                exc,
                "vrc_group_info_last_error_ts",
            )


async def refresh_vrc_group_roles(force: bool = False) -> None:
    if not app_state.vrc_groups_api or vrchat_cooldown_active():
        return

    _ensure_vrc_sync_state()

    async with app_state.vrc_group_roles_refresh_lock:
        if not force and app_state.vrc_group_role_map and not _role_cache_is_stale():
            return

        try:
            roles_response = await _run_vrc_api_call(
                app_state.vrc_groups_api.get_group_roles,
                group_id=GROUP_ID,
            )

            role_objects = getattr(roles_response, "results", None)
            if role_objects is None:
                role_objects = roles_response if isinstance(roles_response, list) else []

            new_map = {
                str(role_id): role_name
                for obj in role_objects
                if (role_id := _extract_role_id(obj)) and (role_name := _extract_role_name(obj))
            }

            if not new_map:
                log.warning("group_roles refresh returned empty role map; keeping existing cache")
                return

            old_count = len(getattr(app_state, "vrc_group_role_map", {}))
            app_state.vrc_group_role_map = new_map
            app_state.vrc_group_roles_last_refresh = _now_ts()
            app_state.vrchat_group_roles = new_map

            try:
                role_names_lower = {str(x).strip().casefold() for x in VRC_STAFF_ROLE_NAMES}
                staff_role_ids = {
                    role_id
                    for role_id, role_name in new_map.items()
                    if str(role_name).strip().casefold() in role_names_lower
                }
                app_state.vrchat_staff_role_ids = staff_role_ids
            except Exception:
                pass

            if hasattr(app_state, "sync_cache_aliases"):
                app_state.sync_cache_aliases()

            if old_count != len(new_map) or old_count == 0:
                log.info("cached %s VRC group roles", len(new_map))
            else:
                log.debug("group_roles refresh complete count=%s", len(new_map))

        except Exception as exc:
            await _send_rate_limited_error(
                "Group Role Cache Error",
                exc,
                "vrc_group_roles_last_error_ts",
            )


async def refresh_vrc_group_members(force: bool = False) -> None:
    if not app_state.vrc_groups_api or vrchat_cooldown_active():
        return

    _ensure_vrc_sync_state()

    async with app_state.vrc_group_members_refresh_lock:
        if not force and app_state.vrc_group_member_roles and not _member_cache_is_stale():
            return

        try:
            await refresh_group_cache_once(force=force)
            await refresh_vrc_group_roles(force=force)

            offset = 0
            batch_size = 100

            old_role_cache = dict(getattr(app_state, "vrc_group_member_roles", {}) or {})
            old_role_id_cache = dict(getattr(app_state, "vrc_group_member_role_ids", {}) or {})

            new_role_cache: dict[str, list[str]] = {}
            new_role_id_cache: dict[str, list[str]] = {}

            total_rows = 0
            missing_user_id = 0
            duplicate_user_ids = 0
            page_count = 0
            consecutive_no_growth_pages = 0

            while True:
                members_response = await _run_vrc_api_call(
                    app_state.vrc_groups_api.get_group_members,
                    group_id=GROUP_ID,
                    n=batch_size,
                    offset=offset,
                )

                members = getattr(members_response, "results", None)
                if members is None:
                    members = members_response if isinstance(members_response, list) else []

                if not members:
                    break

                page_count += 1
                before_count = len(new_role_cache)

                for member in members:
                    total_rows += 1

                    user_id = str(
                        getattr(member, "user_id", None)
                        or getattr(member, "id", None)
                        or ""
                    ).strip()

                    if not user_id:
                        missing_user_id += 1
                        continue

                    if user_id in new_role_cache:
                        duplicate_user_ids += 1

                    new_role_cache[user_id] = _member_role_names_from_obj(member)
                    new_role_id_cache[user_id] = _member_role_ids_from_obj(member)

                    if display_name := _extract_member_display_name(member):
                        app_state.target_name_cache[user_id] = display_name

                loaded_now = len(members)
                growth = len(new_role_cache) - before_count

                log.debug(
                    "group_members page=%s loaded=%s growth=%s offset_before=%s cache_size=%s",
                    page_count,
                    loaded_now,
                    growth,
                    offset,
                    len(new_role_cache),
                )

                consecutive_no_growth_pages = consecutive_no_growth_pages + 1 if growth <= 0 else 0
                offset += loaded_now

                if loaded_now < batch_size:
                    break

                if consecutive_no_growth_pages >= 2:
                    log.warning(
                        "group_members stopping early due to repeated no-growth pages (pages=%s cache_size=%s duplicates=%s)",
                        page_count,
                        len(new_role_cache),
                        duplicate_user_ids,
                    )
                    break

                await asyncio.sleep(GROUP_MEMBERS_PAGE_DELAY_SECONDS)

            if not new_role_cache:
                log.warning("group_members refresh produced empty cache; keeping old cache")
                return

            old_count = len(old_role_cache)

            if old_count > 0 and len(new_role_cache) < max(50, int(old_count * 0.60)):
                log.warning(
                    "group_members refresh looked partial; keeping old cache (new=%s old=%s api_rows=%s duplicates=%s pages=%s)",
                    len(new_role_cache),
                    old_count,
                    total_rows,
                    duplicate_user_ids,
                    page_count,
                )
                return

            # preserve old known staff if VRChat returned a partial membership set
            preserved_staff = 0
            for user_id, old_roles in old_role_cache.items():
                if user_id in new_role_cache:
                    continue

                old_role_ids = old_role_id_cache.get(user_id, [])
                old_is_staff = is_cached_vrc_user_staff(user_id) or any(
                    rid in {str(x).strip() for x in getattr(app_state, "vrchat_staff_role_ids", set())}
                    for rid in old_role_ids
                )

                if old_is_staff:
                    new_role_cache[user_id] = list(old_roles or [])
                    new_role_id_cache[user_id] = list(old_role_ids or [])
                    preserved_staff += 1

            app_state.vrc_group_member_roles = new_role_cache
            app_state.vrc_group_member_role_ids = new_role_id_cache
            app_state.vrc_group_members_last_refresh = _now_ts()
            app_state.vrchat_group_members = new_role_cache

            if hasattr(app_state, "sync_cache_aliases"):
                app_state.sync_cache_aliases()

            log.info(
                "cached %s VRC group members (api_rows=%s missing_user_id=%s duplicates=%s pages=%s old_cache=%s preserved_staff=%s)",
                len(new_role_cache),
                total_rows,
                missing_user_id,
                duplicate_user_ids,
                page_count,
                old_count,
                preserved_staff,
            )

        except Exception as exc:
            await _send_rate_limited_error(
                "Group Member Cache Error",
                exc,
                "vrc_group_members_last_error_ts",
            )


async def ensure_vrc_group_cache_ready() -> None:
    if not app_state.vrc_group_member_roles and not vrchat_cooldown_active():
        await refresh_vrc_group_members(force=True)
    elif not getattr(app_state, "group_cache", None) and not vrchat_cooldown_active():
        await refresh_group_cache_once(force=True)


# ============================================================
# USER RESOLUTION / STATUS SNAPSHOT
# ============================================================

async def resolve_vrchat_user_id(
    vrchat_username: str | None = None,
    vrchat_user_id: str | None = None,
) -> str | None:
    if explicit_id := _normalize_vrc_user_id(vrchat_user_id):
        return explicit_id

    wanted_name = _normalize_vrc_name(vrchat_username)
    if not wanted_name:
        return None

    if not app_state.vrc_group_member_roles:
        await ensure_vrc_group_cache_ready()

    def _scan_cache() -> str | None:
        return next(
            (
                uid
                for uid in app_state.vrc_group_member_roles
                if _normalize_vrc_name(app_state.target_name_cache.get(uid)) == wanted_name
            ),
            None,
        )

    if result := _scan_cache():
        return result

    if _member_cache_is_stale() and not vrchat_cooldown_active():
        await refresh_vrc_group_members(force=True)
        return _scan_cache()

    return None


async def _fetch_vrchat_presence_snapshot(resolved_user_id: str) -> tuple[str, str, str, str]:
    user = await _run_vrc_api_call(app_state.vrc_users_api.get_user, resolved_user_id)
    return (
        _normalize_status_value(getattr(user, "status", "")),
        _normalize_status_value(getattr(user, "location", "")),
        _normalize_status_value(getattr(user, "state", "")),
        _normalize_status_value(getattr(user, "last_platform", "")),
    )


def _classify_presence(
    raw_status: str,
    location: str,
    state: str,
    last_platform: str,
) -> tuple[bool, bool]:
    offline_values = {"offline", "offline:offline"}
    hidden_locations = {"", "private", "traveling"}

    location_is_offline = location in offline_values
    state_is_offline = state in offline_values
    platform_is_offline = last_platform in {"", "offline", "offline:offline"}
    location_is_online_hint = location not in hidden_locations and location not in offline_values
    state_is_online_hint = state not in {"", "offline", "offline:offline"}

    if location_is_online_hint or state_is_online_hint:
        return True, False

    if raw_status in {"ask me", "join me", "busy"} and location_is_offline and state_is_offline:
        return False, False

    if raw_status == "active" and location_is_offline and state_is_offline:
        return False, True

    if raw_status == "active" and not platform_is_offline:
        return True, False

    if raw_status in {"ask me", "join me", "busy"} and not platform_is_offline:
        return False, True

    if raw_status == "offline" and location_is_offline and state_is_offline:
        return False, False

    return False, False


# ============================================================
# STATUS CHECKS
# ============================================================

async def get_vrchat_user_status(
    vrchat_username: str | None = None,
    vrchat_user_id: str | None = None,
) -> tuple[bool, str | None, str | None]:
    if vrchat_cooldown_active():
        log.debug("status check skipped: cooldown active")
        return False, None, None

    if not getattr(app_state, "vrc_users_api", None):
        log.debug("status check skipped: vrc_users_api missing")
        return False, None, None

    resolved_user_id = await resolve_vrchat_user_id(
        vrchat_username=vrchat_username,
        vrchat_user_id=vrchat_user_id,
    )

    log.debug(
        "status resolve input_username=%r input_user_id=%r resolved_user_id=%r",
        vrchat_username,
        vrchat_user_id,
        resolved_user_id,
    )

    if not resolved_user_id:
        return False, None, None

    async def _snapshot_and_process() -> tuple[str, str, str, str, bool, bool]:
        raw_status, location, state, last_platform = await _fetch_vrchat_presence_snapshot(
            resolved_user_id
        )

        is_online, is_ambiguous = _classify_presence(
            raw_status,
            location,
            state,
            last_platform,
        )

        await process_user_status(
            user_id=resolved_user_id,
            ws_online=None,
            friend_presence=None,
            mod_action_recent=_is_vrc_user_recently_active(resolved_user_id),
            audit_actor_recent=None,
            user_status=raw_status,
            last_platform=last_platform,
        )

        log.debug(
            "status result user_id=%s status=%r location=%r state=%r platform=%r online=%s ambiguous=%s",
            resolved_user_id,
            raw_status,
            location,
            state,
            last_platform,
            is_online,
            is_ambiguous,
        )

        return raw_status, location, state, last_platform, is_online, is_ambiguous

    try:
        raw_status, _, _, _, is_online, is_ambiguous = await _snapshot_and_process()

        if is_ambiguous:
            log.debug(
                "ambiguous presence for %s, rechecking in %ss",
                resolved_user_id,
                AMBIGUOUS_STATUS_RECHECK_SECONDS,
            )

            await asyncio.sleep(AMBIGUOUS_STATUS_RECHECK_SECONDS)
            raw_status_2, _, _, _, is_online_2, is_ambiguous_2 = await _snapshot_and_process()

            return True if is_online_2 and not is_ambiguous_2 else False, resolved_user_id, raw_status_2

        return is_online, resolved_user_id, raw_status

    except Exception as exc:
        user_label = vrchat_username or vrchat_user_id or "unknown"

        log.warning(
            "status error user=%s type=%s error=%s",
            user_label,
            type(exc).__name__,
            exc,
        )

        await send_error_log(
            "VRChat User Status Error",
            f"user={user_label} | {type(exc).__name__}: {exc}",
        )

        return False, resolved_user_id, None


async def is_vrchat_user_online(
    vrchat_username: str | None = None,
    vrchat_user_id: str | None = None,
) -> bool:
    resolved_user_id = await resolve_vrchat_user_id(
        vrchat_username=vrchat_username,
        vrchat_user_id=vrchat_user_id,
    )

    if not resolved_user_id:
        log.debug(
            "online_check failed resolve username=%s id=%s",
            vrchat_username,
            vrchat_user_id,
        )
        return False

    entry = _get_pipeline_presence(resolved_user_id)

    if entry and not _pipeline_cache_is_stale():
        is_online = bool(entry.get("online", False))
        log.debug("online_check pipeline user_id=%s online=%s", resolved_user_id, is_online)
        return is_online

    friend_online = await _get_friend_presence_online(resolved_user_id)
    if friend_online is not None:
        log.debug("online_check friend_presence user_id=%s online=%s", resolved_user_id, friend_online)
        return friend_online

    if _is_vrc_user_recently_active(resolved_user_id):
        log.debug("online_check recent_activity user_id=%s", resolved_user_id)
        return True

    is_online, _, raw_status = await get_vrchat_user_status(
        vrchat_username=vrchat_username,
        vrchat_user_id=resolved_user_id,
    )

    log.debug(
        "online_check api_fallback user_id=%s status=%s online=%s",
        resolved_user_id,
        raw_status,
        is_online,
    )

    return is_online


# ============================================================
# LOGIN
# ============================================================

def _finalise_login(api_client: ApiClient, auth_api: AuthenticationApi) -> None:
    from vrchatapi.api.groups_api import GroupsApi
    from vrchatapi.api.users_api import UsersApi

    app_state.vrc_client = api_client
    app_state.vrc_auth_api = auth_api
    app_state.vrc_groups_api = GroupsApi(api_client)
    app_state.vrc_users_api = UsersApi(api_client)

    _ensure_pipeline_state()
    _ensure_recent_activity_state()
    _ensure_vrc_sync_state()
    ensure_pipeline_listener_started()


async def login_vrchat() -> bool:
    vrchat_auth_cookie = os.getenv("VRCHAT_AUTH_COOKIE", "").strip()

    if vrchat_auth_cookie:
        log.info("attempting VRChat login using saved cookie...")

        try:
            config = Configuration()
            config.retries = urllib3.Retry(total=0)

            api_client = ApiClient(config)
            api_client.user_agent = _USER_AGENT
            api_client.default_headers["Cookie"] = f"auth={vrchat_auth_cookie}"

            auth_api = AuthenticationApi(api_client)
            user = await _run_vrc_api_call(auth_api.get_current_user)

            _finalise_login(api_client, auth_api)
            await _refresh_friend_presence_cache(force=True)

            log.info("cookie login success: %s", user.display_name)
            return True

        except Exception as exc:
            log.warning("saved cookie failed: %s — falling back to username/password", exc)

    if not VRCHAT_USERNAME or not VRCHAT_PASSWORD:
        log.warning("VRChat credentials missing")
        await send_error_log(
            "VRChat Credentials Missing",
            "VRCHAT_USERNAME or VRCHAT_PASSWORD not set.",
        )
        return False

    if vrchat_cooldown_active():
        log.warning("VRChat login skipped due to cooldown")
        await send_error_log(
            "VRChat Login Skipped",
            f"Cooldown active: {format_remaining_cooldown()}",
        )
        return False

    log.info("attempting VRChat login via username/password...")

    config = Configuration(username=VRCHAT_USERNAME, password=VRCHAT_PASSWORD)
    config.retries = urllib3.Retry(total=0)

    api_client = ApiClient(config)
    api_client.user_agent = _USER_AGENT
    auth_api = AuthenticationApi(api_client)

    async def _complete_login(user) -> bool:
        _finalise_login(api_client, auth_api)
        await _refresh_friend_presence_cache(force=True)
        await refresh_group_cache_once(force=True)

        log.info("VRChat login successful: %s", user.display_name)

        if cookie := _extract_auth_cookie_from_client():
            log.info("SAVE THIS COOKIE IN YOUR .env")
            log.info("VRCHAT_AUTH_COOKIE=%s", cookie)
        else:
            log.warning("login succeeded but no auth cookie could be extracted")

        return True

    try:
        user = await _run_vrc_api_call(auth_api.get_current_user)
        return await _complete_login(user)

    except Exception as exc:
        err = str(exc)

        if "Email 2 Factor" not in err:
            log.warning("VRChat login failed: %s", err)
            await send_error_log("VRChat Login Failed", err)
            return False

        if not VRCHAT_EMAIL_OTP:
            log.warning("Email 2FA required but no code provided")
            await send_error_log(
                "VRChat 2FA Missing",
                "Email 2FA required but VRCHAT_EMAIL_OTP not set.",
            )
            return False

        log.info("submitting 2FA code...")

        try:
            await _run_vrc_api_call(
                auth_api.verify2_fa_email_code,
                TwoFactorEmailCode(code=VRCHAT_EMAIL_OTP),
            )

            user = await _run_vrc_api_call(auth_api.get_current_user)
            return await _complete_login(user)

        except Exception as otp_exc:
            log.warning("2FA failed: %s", otp_exc)
            await send_error_log("VRChat 2FA Failed", otp_exc)
            return False


# ============================================================
# STAFF LIST FROM VRC GROUP
# ============================================================

async def get_all_vrc_staff_members(force_refresh: bool = False) -> list[dict]:
    """
    Returns all VRChat group members who currently have a staff role.
    Preserves staff detection via role names OR staff role ids.
    """

    if force_refresh:
        await refresh_vrc_group_members(force=True)
    else:
        await ensure_vrc_group_cache_ready()

        if _member_cache_is_stale():
            await refresh_vrc_group_members(force=True)

    wanted_roles = {
        str(role).strip().casefold()
        for role in VRC_STAFF_ROLE_NAMES
    }
    staff_role_ids = {
        str(role_id).strip()
        for role_id in getattr(app_state, "vrchat_staff_role_ids", set())
    }

    results: list[dict] = []

    for user_id, roles in (app_state.vrc_group_member_roles or {}).items():
        normalized_roles = [str(r).strip().casefold() for r in (roles or [])]
        normalized_role_ids = [
            str(rid).strip()
            for rid in (app_state.vrc_group_member_role_ids.get(user_id, []) or [])
        ]

        has_staff_role = any(role in wanted_roles for role in normalized_roles)
        has_staff_role_id = any(role_id in staff_role_ids for role_id in normalized_role_ids)

        if not (has_staff_role or has_staff_role_id):
            continue

        display_name = str(
            app_state.target_name_cache.get(user_id)
            or _fallback_user_name(user_id)
        ).strip()

        results.append({
            "user_id": str(user_id),
            "display_name": display_name,
            "roles": normalized_roles,
            "role_ids": normalized_role_ids,
        })

    results.sort(key=lambda x: x["display_name"].casefold())

    log.info(
        "VRC staff sync found %s staff members",
        len(results),
    )

    return results


# ============================================================
# STARTUP TIMESTAMP
# ============================================================

async def set_startup_timestamp() -> None:
    if not app_state.vrc_groups_api or vrchat_cooldown_active():
        return

    try:
        logs = await _run_vrc_api_call(
            app_state.vrc_groups_api.get_group_audit_logs,
            group_id=GROUP_ID,
            n=1,
            offset=0,
        )

        if getattr(logs, "results", None):
            app_state.startup_timestamp = logs.results[0].created_at
            log.info("startup log marker set to %s", app_state.startup_timestamp)

    except Exception as exc:
        await send_error_log("Startup Timestamp Error", exc)
