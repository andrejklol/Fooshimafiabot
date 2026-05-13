from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import aiohttp
from discord.ext import commands

from core.cache import app_state

log = logging.getLogger("group_stats_sync")

_DEFAULT_GROUP_ID = "grp_23595db4-1452-4fbf-97e1-661fa1b9b074"
_DEFAULT_INTERVAL_SECONDS = 5 * 60  # 5 minutes — coarse, login page caches 60s anyway
_FIRST_RUN_DELAY_SECONDS = 30


def _resolve_group_id() -> str:
    return (os.getenv("VRCHAT_GROUP_ID") or _DEFAULT_GROUP_ID).strip()


def _resolve_interval() -> int:
    raw = os.getenv("VRCHAT_GROUP_STATS_INTERVAL_SECONDS")
    if not raw:
        return _DEFAULT_INTERVAL_SECONDS
    try:
        v = int(str(raw).strip())
        return v if v >= 30 else _DEFAULT_INTERVAL_SECONDS
    except (TypeError, ValueError):
        return _DEFAULT_INTERVAL_SECONDS


async def _fetch_group_member_count(group_id: str) -> tuple[int | None, dict[str, Any]]:
    """Returns (member_count, extras_dict). `extras_dict` includes any
    bonus fields we want to forward (online count, group name).
    On any failure returns (None, {})."""
    api = getattr(app_state, "vrc_groups_api", None)
    if api is None:
        log.debug("[group_stats_sync] vrc_groups_api not initialised yet")
        return None, {}

    # Honour the global VRChat cooldown — `vrchat_cooldown_active`
    # exists in core.utils per existing bot patterns.
    try:
        from core.utils import vrchat_cooldown_active
        if vrchat_cooldown_active():
            log.debug("[group_stats_sync] VRChat cooldown active — skipping tick")
            return None, {}
    except ImportError:
        pass  # No cooldown utility on this bot — proceed.

    try:
        # `_run_vrc_api_call` wraps the sync vrchatapi calls in a
        # threadpool so we don't block the event loop. Reuse the bot's
        # existing helper to keep behaviour consistent with the rest
        # of the codebase.
        try:
            from services.vrchat.vrchat_auth import _run_vrc_api_call
        except ImportError:
            try:
                from services.vrchat_auth import _run_vrc_api_call  # type: ignore
            except ImportError:
                # Last resort: call directly off the event loop. Slower
                # but still works.
                _run_vrc_api_call = None  # type: ignore

        if _run_vrc_api_call is not None:
            group = await _run_vrc_api_call(api.get_group, group_id)
        else:
            loop = asyncio.get_running_loop()
            group = await loop.run_in_executor(None, api.get_group, group_id)
    except Exception as exc:
        log.warning("[group_stats_sync] get_group failed: %s", exc)
        return None, {}

    member_count = (
        getattr(group, "member_count", None)
        or getattr(group, "memberCount", None)
        or getattr(group, "members", None)
    )
    online_count = (
        getattr(group, "online_member_count", None)
        or getattr(group, "onlineMemberCount", None)
    )
    name = getattr(group, "name", None) or getattr(group, "group_name", None)

    if member_count is None:
        log.warning("[group_stats_sync] group object has no member_count attr")
        return None, {}

    try:
        member_count = int(member_count)
    except (TypeError, ValueError):
        return None, {}

    extras: dict[str, Any] = {}
    if online_count is not None:
        try:
            extras["online_member_count"] = int(online_count)
        except (TypeError, ValueError):
            pass
    if name:
        extras["name"] = str(name)
    return member_count, extras


async def _push_to_dashboard(group_id: str, member_count: int, extras: dict[str, Any]) -> None:
    """POST the count to `/sync/group-stats`.

    Reuses the existing `app_state.dashboard_sync` instance for the
    same auth header + base URL the rest of the bot uses. No separate
    HTTP client to manage.
    """
    sync = (
        getattr(app_state, "dashboard_sync", None)
        or getattr(app_state, "dashboard", None)
        or getattr(app_state, "sync", None)
    )
    if sync is None:
        log.warning(
            "[group_stats_sync] no dashboard_sync wired on app_state — "
            "dropping push (count=%s)", member_count,
        )
        return

    payload = {"group_id": group_id, "member_count": member_count, **extras}

    # `_post` is the internal helper that handles retries + the auth
    # header. Call directly to skip the change-hash dedupe path —
    # member counts barely change tick-to-tick but we always want the
    # `updated_at` to advance so the dashboard's 24h freshness gate
    # stays satisfied.
    if hasattr(sync, "_post"):
        try:
            result = await sync._post(
                "sync/group-stats",
                payload,
                cache_key=None,  # bypass dedupe — we want updated_at fresh
            )
            if result.get("success"):
                log.info(
                    "[group_stats_sync] pushed: group=%s member_count=%d %s",
                    group_id, member_count, extras,
                )
            else:
                log.warning(
                    "[group_stats_sync] push failed: %s", result.get("error") or result,
                )
        except Exception:
            log.exception("[group_stats_sync] push raised")
        return

    # Fallback for older sync clients without `_post` — raw aiohttp.
    base = getattr(sync, "base_url", None)
    headers = getattr(sync, "headers", None)
    if not base or not headers:
        log.warning("[group_stats_sync] dashboard_sync has no base_url/headers — cannot push raw")
        return
    try:
        async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.post(f"{base}/sync/group-stats", json=payload) as resp:
                if 200 <= resp.status < 300:
                    log.info(
                        "[group_stats_sync] pushed via raw fallback: count=%d", member_count,
                    )
                else:
                    log.warning(
                        "[group_stats_sync] raw push status=%s body=%s",
                        resp.status, await resp.text(),
                    )
    except Exception:
        log.exception("[group_stats_sync] raw push raised")


class GroupStatsSync(commands.Cog):
    """Periodic VRChat group member count → dashboard syncer."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._task: asyncio.Task | None = None
        self._group_id = _resolve_group_id()
        self._interval = _resolve_interval()

    async def cog_load(self) -> None:
        # `cog_load` runs in a discord.py 2.x lifecycle hook before
        # the cog starts receiving events. Spawn the loop here so it
        # outlives the load() call but isn't blocking it.
        self._task = self.bot.loop.create_task(self._run_loop(), name="group_stats_sync")

    async def cog_unload(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _run_loop(self) -> None:
        log.info(
            "[group_stats_sync] loop started (group_id=%s interval=%ss)",
            self._group_id, self._interval,
        )
        # Cold-start delay so vrc_groups_api has time to login.
        await asyncio.sleep(_FIRST_RUN_DELAY_SECONDS)

        while True:
            try:
                count, extras = await _fetch_group_member_count(self._group_id)
                if count is not None:
                    await _push_to_dashboard(self._group_id, count, extras)
            except asyncio.CancelledError:
                log.info("[group_stats_sync] loop canceled")
                break
            except Exception:
                log.exception("[group_stats_sync] loop iteration crashed (will retry)")
            await asyncio.sleep(self._interval)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GroupStatsSync(bot))
    log.info(
        "[group_stats_sync] cog loaded — group=%s interval=%ss",
        _resolve_group_id(), _resolve_interval(),
    )
