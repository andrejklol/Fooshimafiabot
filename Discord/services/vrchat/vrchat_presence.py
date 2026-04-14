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
# CACHE STALENESS
# ============================================================

def _pipeline_cache_is_stale() -> bool:
    last = float(getattr(app_state, "vrc_pipeline_last_event_ts", 0.0) or 0.0)
    return last <= 0 or (_now_ts() - last) >= PIPELINE_STALE_SECONDS


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
                            morsel = parsed.get(cookie_name)
                            if morsel and morsel.value:
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
                    morsel = parsed.get(cookie_name)
                    if morsel and morsel.value:
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

    auth_cookie = _extract_auth_cookie_from_client()
    if auth_cookie:
        cookie = SimpleCookie()
        cookie["auth"] = auth_cookie
        headers["Cookie"] = cookie.output(header="").strip()

    return headers


def _build_pipeline_url() -> str | None:
    auth_cookie = _extract_auth_cookie_from_client()
    if not auth_cookie:
        return None

    return f"{PIPELINE_URL_BASE}?authToken={quote(auth_cookie, safe='')}"


# ============================================================
# RECENT ACTIVITY SIGNAL
# ============================================================

def mark_vrc_user_recently_active(user_id: str) -> None:
    _ensure_recent_activity_state()

    cleaned = str(user_id or "").strip()
    if cleaned:
        app_state.vrc_recent_activity[cleaned] = _now_ts()
        log.debug("recent_activity marked user_id=%s", cleaned)


def _is_vrc_user_recently_active(user_id: str) -> bool:
    _ensure_recent_activity_state()

    cleaned = str(user_id or "").strip()
    if not cleaned:
        return False

    ts = float(app_state.vrc_recent_activity.get(cleaned, 0.0) or 0.0)
    is_recent = ts > 0 and (_now_ts() - ts) <= RECENT_ACTIVITY_WINDOW_SECONDS

    log.debug(
        "recent_activity check user_id=%s last_seen=%s recent=%s",
        cleaned,
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

    cleaned_user_id = str(user_id or "").strip()
    if not cleaned_user_id:
        return False

    old = app_state.vrc_pipeline_friend_presence.get(cleaned_user_id, {})
    new_location = str(location or "").strip().lower()
    new_platform = str(platform or "").strip().lower()

    changed = (
        old.get("online") != bool(online)
        or old.get("location") != new_location
        or old.get("platform") != new_platform
        or old.get("source_event") != source_event
    )

    app_state.vrc_pipeline_friend_presence[cleaned_user_id] = {
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

    cleaned_user_id = str(user_id or "").strip()
    if not cleaned_user_id:
        return None

    entry = app_state.vrc_pipeline_friend_presence.get(cleaned_user_id)
    if not entry:
        return None

    updated_at = float(entry.get("updated_at", 0.0) or 0.0)
    if updated_at <= 0 or (_now_ts() - updated_at) >= PIPELINE_STALE_SECONDS:
        return None

    return entry


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

    content = payload.get("content")
    if isinstance(content, str) and content.strip():
        try:
            payload["content"] = json.loads(content.strip())
        except Exception:
            pass

    return payload


def _schedule_status_update(
    *,
    user_id: str,
    ws_online: bool | None,
    friend_presence: bool | None,
    user_status: str | None,
    last_platform: str | None,
) -> None:
    asyncio.create_task(
        process_user_status(
            user_id=user_id,
            ws_online=ws_online,
            friend_presence=friend_presence,
            user_status=user_status,
            last_platform=last_platform,
        )
    )


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

    user_obj = content.get("user")
    if isinstance(user_obj, dict):
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
        _schedule_status_update(
            user_id=user_id,
            ws_online=True,
            friend_presence=True,
            user_status="active",
            last_platform=platform,
        )
        if changed:
            log.info(
                "pipeline friend-online user_id=%s location=%r platform=%r",
                user_id,
                location,
                platform,
            )
        return

    if event_type == "friend-location":
        online = location not in offline_locations
        changed = _set_pipeline_presence(
            user_id,
            online=online,
            source_event=event_type,
            location=location,
            platform=platform,
        )
        _schedule_status_update(
            user_id=user_id,
            ws_online=online,
            friend_presence=online,
            user_status="active" if online else "offline",
            last_platform=platform,
        )
        if changed:
            log.info(
                "pipeline friend-location user_id=%s location=%r online=%s",
                user_id,
                location,
                online,
            )
        return

    if event_type == "friend-active":
        effective_platform = platform or "web"
        changed = _set_pipeline_presence(
            user_id,
            online=True,
            source_event=event_type,
            location=location,
            platform=effective_platform,
        )
        _schedule_status_update(
            user_id=user_id,
            ws_online=None,
            friend_presence=True,
            user_status="active",
            last_platform=effective_platform,
        )
        if changed:
            log.info(
                "pipeline friend-active user_id=%s platform=%r",
                user_id,
                effective_platform,
            )
        return

    if event_type == "friend-offline":
        changed = _set_pipeline_presence(
            user_id,
            online=False,
            source_event=event_type,
            location="offline",
            platform=platform,
        )
        _schedule_status_update(
            user_id=user_id,
            ws_online=False,
            friend_presence=False,
            user_status="offline",
            last_platform=platform,
        )
        if changed:
            log.info("pipeline friend-offline user_id=%s", user_id)
        return

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
                    payload = _decode_pipeline_message(raw_message)
                    if payload:
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

        offline = (
            getattr(me, "offline_friends", None)
            or getattr(me, "offlineFriends", None)
            or []
        )
        online = (
            getattr(me, "online_friends", None)
            or getattr(me, "onlineFriends", None)
            or []
        )
        active = (
            getattr(me, "active_friends", None)
            or getattr(me, "activeFriends", None)
            or []
        )

        cache: dict[str, bool] = {}

        for uid in offline:
            cleaned = str(uid or "").strip()
            if cleaned:
                cache[cleaned] = False

        for uid in (*online, *active):
            cleaned = str(uid or "").strip()
            if cleaned:
                cache[cleaned] = True

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
    cleaned_user_id = str(user_id or "").strip()

    if not cleaned_user_id or cleaned_user_id not in cache:
        return None

    is_online = bool(cache[cleaned_user_id])
    log.debug("friend_presence user_id=%s is_online=%s", cleaned_user_id, is_online)
    return is_online


# ============================================================
# USER RESOLUTION / STATUS SNAPSHOT
# ============================================================

async def resolve_vrchat_user_id(
    vrchat_username: str | None = None,
    vrchat_user_id: str | None = None,
) -> str | None:
    explicit_id = _normalize_vrc_user_id(vrchat_user_id)
    if explicit_id:
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

    result = _scan_cache()
    if result:
        return result

    if _member_cache_is_stale() and not vrchat_cooldown_active():
        await refresh_vrc_group_members(force=True)
        return _scan_cache()

    return None


async def _fetch_vrchat_presence_snapshot(
    resolved_user_id: str,
) -> tuple[str, str, str, str]:
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

            return (
                True if is_online_2 and not is_ambiguous_2 else False,
                resolved_user_id,
                raw_status_2,
            )

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
        log.debug(
            "online_check friend_presence user_id=%s online=%s",
            resolved_user_id,
            friend_online,
        )
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


__all__ = [
    "ensure_pipeline_listener_started",
    "get_vrchat_user_status",
    "is_vrchat_user_online",
    "mark_vrc_user_recently_active",
    "resolve_vrchat_user_id",
    "stop_pipeline_listener",
    "_extract_auth_cookie_from_client",
    "_refresh_friend_presence_cache",
]
