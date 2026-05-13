from __future__ import annotations

import asyncio
import json
import logging
from http.cookies import SimpleCookie
from typing import Any
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
    _normalize_vrc_name,  # noqa: F401  (kept for parity with the original module)
    _normalize_vrc_user_id,
    _now_ts,
)
from .vrchat_group import (
    _member_cache_is_stale,  # noqa: F401  (kept for parity)
    ensure_vrc_group_cache_ready,  # noqa: F401  (kept for parity)
    refresh_vrc_group_members,  # noqa: F401  (kept for parity)
)

log = logging.getLogger("vrchat_presence")

AMBIGUOUS_STATUS_RECHECK_SECONDS = 6

PIPELINE_URL_BASE = "wss://pipeline.vrchat.cloud/"
PIPELINE_RECONNECT_SECONDS = 5
PIPELINE_STALE_SECONDS = 180

RECENT_ACTIVITY_WINDOW_SECONDS = 20 * 60
FRIEND_PRESENCE_CACHE_SECONDS = 90

# How often the self-driven background loop forces a fresh friend list
# REST poll. 60s gives us at most a 60s lag on `last_seen` even when no
# pipeline events arrive (e.g. all staff offline). Independent of the
# 90s lazy-cache TTL above — the lazy TTL still applies to direct
# callers of `_refresh_friend_presence_cache(force=False)` so we don't
# hammer the API when many code paths request status simultaneously.
FRIEND_PRESENCE_REFRESH_SECONDS = 60

# How often the pipeline watchdog wakes up to check for staleness.
PIPELINE_WATCHDOG_INTERVAL_SECONDS = 30


# ============================================================
# PIPELINE CACHE
# ============================================================
def _pipeline_cache_is_stale() -> bool:
    last = float(getattr(app_state, "vrc_pipeline_last_event_ts", 0.0) or 0.0)
    return last <= 0 or (_now_ts() - last) >= PIPELINE_STALE_SECONDS


# ============================================================
# RECENT ACTIVITY TRACKING
# ============================================================
def mark_vrc_user_recently_active(user_id: str) -> None:
    _ensure_recent_activity_state()
    uid = _normalize_vrc_user_id(user_id)
    if uid:
        if hasattr(app_state, "vrc_recent_activity"):
            app_state.vrc_recent_activity[uid] = _now_ts()
        else:
            app_state.vrc_user_recent_activity[uid] = _now_ts()


def _recent_activity(user_id: str) -> bool:
    _ensure_recent_activity_state()
    uid = _normalize_vrc_user_id(user_id)
    if not uid:
        return False
    if hasattr(app_state, "vrc_recent_activity"):
        ts = app_state.vrc_recent_activity.get(uid, 0.0)
    else:
        ts = getattr(app_state, "vrc_user_recent_activity", {}).get(uid, 0.0)
    return (_now_ts() - ts) < RECENT_ACTIVITY_WINDOW_SECONDS


# ============================================================
# LAST-OBSERVED TIMESTAMP (NEW — fixes "stale last_seen" report)
# ============================================================
def _ensure_observed_state() -> None:
    """Lazy-init the observation timestamp dict on app_state.

    Stored as `{uid: float epoch_seconds}`. Bumped every time the bot
    has fresh evidence the user exists on VRChat — i.e. every pipeline
    event, every successful REST friend-list snapshot.
    """
    if not hasattr(app_state, "vrc_user_last_observed"):
        app_state.vrc_user_last_observed = {}


def mark_vrc_user_observed(user_id: str, ts: float | None = None) -> None:
    """Stamp 'I've just seen this user on the VRChat side' = now.

    Public helper so other modules (auth refresh, group cache,
    moderation handlers) can also extend the observation horizon.
    """
    _ensure_observed_state()
    uid = _normalize_vrc_user_id(user_id)
    if uid:
        app_state.vrc_user_last_observed[uid] = float(ts) if ts else _now_ts()


def get_vrc_user_last_observed_ts(user_id: str) -> float | None:
    """Returns the last time the bot observed this VRChat user, as a
    Unix epoch float. ``None`` if we've never seen them.

    INTENDED USAGE in ``dashboard_sync.py``: when building the
    ``last_seen`` field of each /sync/vrchat-status payload row,
    prefer this value over the (frozen) `LimitedUser.last_login`:

        from .vrchat_presence import get_vrc_user_last_observed_ts
        from datetime import datetime, timezone

        ts = get_vrc_user_last_observed_ts(vrc_id)
        last_seen = (
            datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            if ts else None
        )

    This guarantees `last_seen` advances on every pipeline event and
    every REST poll, instead of freezing at a snapshot timestamp.
    """
    _ensure_observed_state()
    uid = _normalize_vrc_user_id(user_id)
    if not uid:
        return None
    ts = app_state.vrc_user_last_observed.get(uid)
    return float(ts) if ts else None


# ============================================================
# FRIEND PRESENCE CACHE FETCHERS
# ============================================================
async def _refresh_friend_presence_cache(force: bool = False) -> None:
    """Snapshot friend presence into app_state.

    Three pieces of state are written:
      • `vrc_online_friend_ids`   — set[str] of user IDs from
        `get_friends(offline=False)`. Authoritative "is online".
      • `vrc_offline_friend_ids`  — set[str] of user IDs from
        `get_friends(offline=True)`. Used only for diagnostics.
      • `vrc_pipeline_friend_presence_cache` — dict[user_id, LimitedUser]
        carrying the freshest object we have for that user, so we can
        still read `status`, `last_platform`, etc.

    Also stamps `app_state.vrc_user_last_observed[uid] = now` for
    every friend that came back in either list — that's the source
    of truth for `last_seen` exported via
    `get_vrc_user_last_observed_ts()`.

    The two sets, together, are how we answer "is X online?" — never
    by reading a (missing) `is_online` attribute on the object.

    ``force=True`` (used by the background refresh loop) bypasses BOTH
    the lazy TTL and the global VRChat cooldown — the friend-list
    endpoint is cheap (2 calls/min at this cadence) and well under
    VRChat's documented rate limits. The cooldown otherwise can
    persist for minutes after a single 429 anywhere in the bot,
    silently freezing the dashboard.
    """
    # Lazy callers honour the cooldown; the background refresh loop
    # forces through it because cache freshness is what it exists to
    # protect.
    if not force and vrchat_cooldown_active():
        return

    _ensure_pipeline_state()
    _ensure_observed_state()
    last = float(getattr(app_state, "vrc_friend_presence_last_refresh", 0.0) or 0.0)
    if not force and (_now_ts() - last) < FRIEND_PRESENCE_CACHE_SECONDS:
        return

    if not getattr(app_state, "vrc_friends_api", None):
        return

    try:
        # VRChat caps `n` at 100; one page is enough for ~100 friends. If
        # you have more, paginate by bumping `offset` until the page
        # comes back short.
        online_friends = await _run_vrc_api_call(
            app_state.vrc_friends_api.get_friends,
            offset=0, n=100, offline=False,
        )
        offline_friends = await _run_vrc_api_call(
            app_state.vrc_friends_api.get_friends,
            offset=0, n=100, offline=True,
        )

        online_ids: set[str] = set()
        offline_ids: set[str] = set()
        objects: dict[str, Any] = {}
        now = _now_ts()

        # ── Online list first → stamps the canonical object ──
        if isinstance(online_friends, list):
            for f in online_friends:
                f_id = _normalize_vrc_user_id(
                    getattr(f, "id", None) or getattr(f, "user_id", None)
                )
                if f_id:
                    online_ids.add(f_id)
                    objects[f_id] = f
                    app_state.vrc_user_last_observed[f_id] = now

        # ── Offline list next. We only fill objects for users NOT in
        #    the online set so a user briefly returned by both endpoints
        #    keeps the online object. ──
        if isinstance(offline_friends, list):
            for f in offline_friends:
                f_id = _normalize_vrc_user_id(
                    getattr(f, "id", None) or getattr(f, "user_id", None)
                )
                if not f_id:
                    continue
                offline_ids.add(f_id)
                if f_id not in objects:
                    objects[f_id] = f
                # Even offline friends count as "observed" — we just
                # confirmed the friendship still exists and they're
                # explicitly offline (vs. unknown / stale data).
                app_state.vrc_user_last_observed[f_id] = now

        app_state.vrc_online_friend_ids = online_ids
        app_state.vrc_offline_friend_ids = offline_ids
        app_state.vrc_pipeline_friend_presence_cache = objects
        app_state.vrc_friend_presence_last_refresh = now

        log.info(
            "Friend presence cache synced — %d online, %d offline, "
            "%d total cached objects",
            len(online_ids), len(offline_ids), len(objects),
        )
    except Exception as exc:
        log.warning("Failed to refresh friend presence maps: %s", exc)


def _get_cached_friend_object(user_id: str) -> Any | None:
    cache = getattr(app_state, "vrc_pipeline_friend_presence_cache", None) or {}
    return cache.get(_normalize_vrc_user_id(user_id))


def _is_user_online_via_cache(user_id: str) -> bool | None:
    """Returns True/False if we have authoritative cache data for this
    user, None if we don't and the caller should fall back to the
    direct `users_api.get_user` lookup."""
    uid = _normalize_vrc_user_id(user_id)
    if not uid:
        return None
    online = getattr(app_state, "vrc_online_friend_ids", None)
    offline = getattr(app_state, "vrc_offline_friend_ids", None)
    if online is None and offline is None:
        return None
    if online and uid in online:
        return True
    if offline and uid in offline:
        return False
    return None


# ============================================================
# WEBSOCKET PIPELINE LISTENER
# ============================================================
async def _pipeline_loop() -> None:
    _ensure_pipeline_state()
    _ensure_observed_state()
    while True:
        try:
            cookies = getattr(app_state, "vrc_cookies", None)
            if not cookies:
                log.debug("Pipeline is waiting for vrc_cookies to initialize...")
                await asyncio.sleep(2)
                continue

            auth_token = ""
            if isinstance(cookies, dict):
                auth_token = cookies.get("auth", "")
            else:
                try:
                    auth_token = cookies.get("auth").value
                except Exception:
                    auth_token = str(cookies)

            if not auth_token:
                log.debug("Pipeline is waiting for live auth token string...")
                await asyncio.sleep(2)
                continue

            cookie_obj = SimpleCookie()
            if isinstance(cookies, dict):
                for k, v in cookies.items():
                    cookie_obj[k] = v
            cookie_str = cookie_obj.output(header="", sep=";").strip()

            ua_string = str(
                _USER_AGENT
                or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Fooshimafiabot/2.0"
            )

            headers = {
                "User-Agent": ua_string,
                "Cookie": cookie_str,
                "Origin": "https://vrchat.com",
            }

            url = f"{PIPELINE_URL_BASE}?authToken={quote(auth_token)}"
            log.info("Connecting to VRChat Streaming Pipeline...")

            async with websockets.connect(
                url,
                additional_headers=headers,
                ping_interval=20,
                ping_timeout=20,
            ) as ws:
                app_state.vrc_pipeline_ws = ws
                app_state.vrc_pipeline_last_event_ts = _now_ts()
                log.info("VRChat Streaming Pipeline Online.")

                # Force a fresh REST snapshot the moment the pipeline
                # opens — without this we'd have to wait up to 90s for
                # the first cache TTL to expire after a reconnect.
                try:
                    await _refresh_friend_presence_cache(force=True)
                except Exception:
                    log.exception("Initial post-pipeline cache refresh failed")

                async for message in ws:
                    app_state.vrc_pipeline_last_event_ts = _now_ts()
                    try:
                        data = json.loads(message)
                        ev_type = data.get("type")
                        content_str = data.get("content", "{}")
                        content = (
                            json.loads(content_str)
                            if isinstance(content_str, str)
                            else content_str
                        )

                        u_id = _normalize_vrc_user_id(
                            content.get("userId") or content.get("id")
                        )
                        if not u_id:
                            continue

                        # Every pipeline event (online OR offline) is
                        # fresh evidence the user exists. Stamp
                        # last_observed regardless of event type.
                        app_state.vrc_user_last_observed[u_id] = _now_ts()

                        # Keep the set view of online IDs in sync with
                        # pipeline events so the next call to
                        # `_is_user_online_via_cache` doesn't have to wait
                        # for the next REST poll.
                        if ev_type in ("user-online", "user-update",
                                       "friend-online", "friend-active"):
                            status = _normalize_status_value(
                                content.get("status") or "active"
                            )
                            platform = _normalize_status_value(
                                content.get("last_platform")
                            )
                            on_set = getattr(app_state, "vrc_online_friend_ids", None)
                            off_set = getattr(app_state, "vrc_offline_friend_ids", None)
                            if isinstance(on_set, set):
                                on_set.add(u_id)
                            if isinstance(off_set, set):
                                off_set.discard(u_id)
                            await process_user_status(
                                user_id=u_id,
                                ws_online=True,
                                user_status=status,
                                last_platform=platform,
                            )
                        elif ev_type in ("user-offline", "friend-offline"):
                            on_set = getattr(app_state, "vrc_online_friend_ids", None)
                            off_set = getattr(app_state, "vrc_offline_friend_ids", None)
                            if isinstance(on_set, set):
                                on_set.discard(u_id)
                            if isinstance(off_set, set):
                                off_set.add(u_id)
                            await process_user_status(
                                user_id=u_id,
                                ws_online=False,
                                user_status="offline",
                            )
                    except Exception as inner:
                        log.error("Error handling pipeline payload message: %s", inner)

        except asyncio.CancelledError:
            log.info("Pipeline event handler task canceled.")
            break
        except Exception as exc:
            log.warning(
                "Pipeline connection dropped: %s. Retrying in %ss...",
                exc, PIPELINE_RECONNECT_SECONDS,
            )
            app_state.vrc_pipeline_ws = None
            await asyncio.sleep(PIPELINE_RECONNECT_SECONDS)


# ============================================================
# BACKGROUND REFRESH + WATCHDOG (NEW — fixes the 18-min freeze)
# ============================================================
async def _friend_presence_refresh_loop() -> None:
    """Self-driven REST refresh — independent of any caller.

    Without this loop, `_refresh_friend_presence_cache()` only fires
    when something explicitly calls `get_vrchat_user_status()`. The
    bot's `dashboard_sync.py` reads `app_state.vrc_*` dicts directly
    for performance, so per-user calls don't happen on the push path.
    Result: the cache freezes at the last incidental refresh — exactly
    the symptom we're fixing.

    Runs every ``FRIEND_PRESENCE_REFRESH_SECONDS`` (60s by default).
    Calls with ``force=True`` so it bypasses the global VRChat
    cooldown — the friend-list endpoint is cheap and we MUST keep
    the dashboard fresh.
    """
    log.info(
        "Friend presence refresh loop started (interval=%ss).",
        FRIEND_PRESENCE_REFRESH_SECONDS,
    )
    while True:
        try:
            t0 = _now_ts()
            await _refresh_friend_presence_cache(force=True)
            log.info(
                "Friend presence refresh tick OK in %.2fs "
                "(online=%d offline=%d cached=%d)",
                _now_ts() - t0,
                len(getattr(app_state, "vrc_online_friend_ids", None) or ()),
                len(getattr(app_state, "vrc_offline_friend_ids", None) or ()),
                len(getattr(app_state, "vrc_pipeline_friend_presence_cache", None) or {}),
            )
        except asyncio.CancelledError:
            log.info("Friend presence refresh loop canceled.")
            break
        except Exception:
            log.exception("Friend presence refresh loop iteration failed")
        await asyncio.sleep(FRIEND_PRESENCE_REFRESH_SECONDS)


async def _pipeline_watchdog_loop() -> None:
    """Force-reconnect the websocket if it's silently half-dead.

    A half-open WebSocket connection looks alive (no `ConnectionClosed`
    raised) but stops delivering events. Without this watchdog the
    pipeline stays "connected" for hours while the dashboard goes
    increasingly stale.

    Detects "no event in PIPELINE_STALE_SECONDS" and closes the
    underlying websocket. The next iteration of `_pipeline_loop`
    re-establishes the connection from scratch.
    """
    _ensure_pipeline_state()
    while True:
        try:
            ws = getattr(app_state, "vrc_pipeline_ws", None)
            if ws is not None and _pipeline_cache_is_stale():
                log.warning(
                    "Pipeline watchdog: no events in %ss — forcing reconnect.",
                    PIPELINE_STALE_SECONDS,
                )
                try:
                    await ws.close()
                except Exception:
                    log.exception("Pipeline watchdog ws.close() failed")
                # Clear the pointer so a stuck loop iteration sees None
                # and rebuilds; `_pipeline_loop` re-stamps it on success.
                app_state.vrc_pipeline_ws = None
        except asyncio.CancelledError:
            log.info("Pipeline watchdog loop canceled.")
            break
        except Exception:
            log.exception("Pipeline watchdog iteration failed")
        await asyncio.sleep(PIPELINE_WATCHDOG_INTERVAL_SECONDS)


def ensure_pipeline_listener_started() -> None:
    """Start the pipeline + watchdog + refresh loops. Idempotent."""
    _ensure_pipeline_state()
    _ensure_observed_state()

    # Pipeline (already in the prior version of this patch).
    if app_state.vrc_pipeline_task is None or app_state.vrc_pipeline_task.done():
        app_state.vrc_pipeline_task = asyncio.create_task(_pipeline_loop())

    # Watchdog (NEW).
    wd = getattr(app_state, "vrc_pipeline_watchdog_task", None)
    if wd is None or wd.done():
        app_state.vrc_pipeline_watchdog_task = asyncio.create_task(
            _pipeline_watchdog_loop()
        )

    # Background REST refresh (NEW).
    rl = getattr(app_state, "vrc_friend_presence_refresh_task", None)
    if rl is None or rl.done():
        app_state.vrc_friend_presence_refresh_task = asyncio.create_task(
            _friend_presence_refresh_loop()
        )


def stop_pipeline_listener() -> None:
    _ensure_pipeline_state()
    if app_state.vrc_pipeline_task:
        app_state.vrc_pipeline_task.cancel()
        app_state.vrc_pipeline_task = None
    wd = getattr(app_state, "vrc_pipeline_watchdog_task", None)
    if wd:
        wd.cancel()
        app_state.vrc_pipeline_watchdog_task = None
    rl = getattr(app_state, "vrc_friend_presence_refresh_task", None)
    if rl:
        rl.cancel()
        app_state.vrc_friend_presence_refresh_task = None
    app_state.vrc_pipeline_ws = None


# ============================================================
# PRIMARY PUBLIC STATUS FETCH ENGINE
# ============================================================
async def get_vrchat_user_status(
    vrchat_username: str | None = None,
    vrchat_user_id: str | None = None,
) -> tuple[bool, str | None, str | None]:
    """Evaluate online status for a targeted user context.

    Returns ``(is_online, resolved_user_id, status_string)``. Online
    state comes from the `vrc_online_friend_ids` set (populated by
    `get_friends(offline=False)` + pipeline events). Status / platform
    come from the cached `LimitedUser` object. We only call
    `users_api.get_user` if the user isn't a friend (rare for staff).
    """
    uid = _normalize_vrc_user_id(vrchat_user_id)
    if not uid and vrchat_username:
        from .vrchat_group import resolve_vrchat_user_id
        uid = await resolve_vrchat_user_id(vrchat_username)

    if not uid:
        return False, None, None

    await _refresh_friend_presence_cache()

    # Authoritative online/offline via list membership.
    cache_state = _is_user_online_via_cache(uid)
    friend_obj = _get_cached_friend_object(uid)

    if cache_state is not None:
        friend_online = bool(cache_state)
        # Status fallback chain: pipeline event > cached object > derived.
        status_raw = ""
        if friend_obj is not None:
            status_raw = getattr(friend_obj, "status", "") or ""
        status = _normalize_status_value(
            status_raw or ("active" if friend_online else "offline")
        )
        last_platform = ""
        if friend_obj is not None:
            last_platform = getattr(friend_obj, "last_platform", "") or ""
        last_platform = _normalize_status_value(last_platform)

        # We just confirmed this user's state via the cache → bump
        # last_observed so `dashboard_sync.py` publishes a fresh
        # `last_seen`.
        mark_vrc_user_observed(uid)

        await process_user_status(
            user_id=uid,
            friend_presence=friend_online,
            user_status=status,
            mod_action_recent=_recent_activity(uid),
            last_platform=last_platform,
        )
        # `is_online` is the AND of "in online list" and "status not
        # explicitly offline" so a friend who set status=offline
        # manually still reads as offline.
        return friend_online and status != "offline", uid, status

    # Not in either friend list (probably not a friend) → direct lookup.
    if not getattr(app_state, "vrc_users_api", None):
        log.debug("VRChat status check skipped: Users API not yet initialized.")
        return False, uid, "initializing"

    try:
        user = await _run_vrc_api_call(app_state.vrc_users_api.get_user, uid)
        status = _normalize_status_value(getattr(user, "status", ""))
        last_platform = _normalize_status_value(getattr(user, "last_platform", ""))
        # Full `User` model DOES expose `state`/`is_online`. Prefer
        # `state == "online"` since `is_online` has historically been
        # spotty across SDK versions.
        state = (getattr(user, "state", "") or "").lower()
        is_online_flag = state == "online" or bool(getattr(user, "is_online", False))

        # Direct API hit also counts as fresh observation.
        mark_vrc_user_observed(uid)

        await process_user_status(
            user_id=uid,
            user_status=status,
            friend_presence=is_online_flag,
            mod_action_recent=_recent_activity(uid),
            last_platform=last_platform,
        )
        return is_online_flag and status != "offline", uid, status

    except Exception as exc:
        await send_error_log("VRChat status error", exc)
        return False, uid, None


async def is_vrchat_user_online(
    vrchat_username: str | None = None,
    vrchat_user_id: str | None = None,
) -> bool:
    online, _, _ = await get_vrchat_user_status(vrchat_username, vrchat_user_id)
    return online


__all__ = [
    "ensure_pipeline_listener_started",
    "get_vrchat_user_status",
    "get_vrc_user_last_observed_ts",
    "is_vrchat_user_online",
    "mark_vrc_user_observed",
    "mark_vrc_user_recently_active",
    "stop_pipeline_listener",
]
