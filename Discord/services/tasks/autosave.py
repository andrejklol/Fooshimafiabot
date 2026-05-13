from __future__ import annotations

import asyncio
import logging
import unicodedata
from datetime import datetime, timezone

from core.cache import app_state
from cogs.presence_warmup import get_cached_status as _cached_presence
from services.leaderboard.storage import leaderboard_data, save_leaderboard_data
from services.offenders.storage import repeat_offenders, save_repeat_offenders

log = logging.getLogger("autosave")

# ─── Identity map helpers ─────────────────────────────────────────────────

def _build_vrc_to_discord_map() -> dict[str, str]:
    """vrc_id → discord_id. Sources, in priority order:
    1. STAFF_ALERT_ORDER hardcoded pairs (authoritative)
    2. leaderboard_data["staff"] entries that already have discord_id
    """
    from core.config import STAFF_ALERT_ORDER

    vrc_to_discord: dict[str, str] = {}

    for action_groups in STAFF_ALERT_ORDER.values():
        for _, members in action_groups:
            for member in members:
                vrc_id = str(member.get("vrchat_user_id") or "").strip()
                disc_id = str(member.get("discord_id") or "").strip()
                if vrc_id and disc_id:
                    vrc_to_discord[vrc_id] = disc_id

    staff_section = leaderboard_data.get("staff") or {}
    if isinstance(staff_section, dict):
        for vrc_id, entry in staff_section.items():
            if not isinstance(entry, dict):
                continue
            vrc_id = str(vrc_id or "").strip()
            disc_id = str(entry.get("discord_id") or "").strip()
            if not vrc_id or not disc_id:
                continue
            vrc_to_discord.setdefault(vrc_id, disc_id)

    return vrc_to_discord


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_str(value, default: str = "Unknown") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _normalize_name_key(value: str | None) -> str:
    """Same NFKC-aware normalizer as staff_sync.py — used here for the
    name-fallback Discord lookup so stylized Unicode display names
    still match plain text in the guild."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.strip().lower()
    return "".join(ch for ch in text if ch.isalnum())


# ─── Discord presence via guild gateway ───────────────────────────────────

async def _get_discord_presence(bot, discord_id: str):
    """Fetch (status, avatar_url) for a Discord user.

    Status resolution order:
      1. PRESENCE_CACHE (kept fresh by `on_presence_update` in the
         presence_warmup cog) — authoritative for any user who has
         changed status while the bot was running.
      2. `member.status` from the guild member cache — fallback for
         users who happened to be in the chunk response but haven't
         pushed a PRESENCE_UPDATE since.
      3. "offline" — final fallback if neither cache has data.

    The avatar URL is always resolved from the guild member object
    (the cache only tracks status).
    """
    if not discord_id:
        return "offline", None
    try:
        did_int = int(discord_id)
    except (TypeError, ValueError):
        return "offline", None

    # 1) Check the live presence cache first.
    cached = _cached_presence(did_int)

    # 2) Look up member object for avatar + member-cache fallback.
    member = None
    for guild in bot.guilds:
        m = guild.get_member(did_int)
        if m is not None:
            member = m
            break

    if member is None:
        return cached or "offline", None

    # 3) Pick the best status we have.
    if cached:
        status = cached
    else:
        raw = str(member.status).lower()
        status = "dnd" if raw == "do_not_disturb" else (
            "offline" if raw == "invisible" else raw
        )

    avatar_url = None
    if member.avatar:
        avatar_url = member.avatar.url
    elif member.display_avatar:
        avatar_url = member.display_avatar.url

    return status, avatar_url


def _resolve_discord_id_via_guild(bot, vrc_username: str | None, vrc_id: str | None) -> str | None:
    """Best-effort Discord ID lookup for staff via NFKC name match."""
    if not bot or not vrc_username:
        return None
    target_key = _normalize_name_key(vrc_username)
    if not target_key:
        return None

    for guild in bot.guilds:
        for member in getattr(guild, "members", []):
            for candidate_name in (
                getattr(member, "global_name", None),
                getattr(member, "display_name", None),
                getattr(member, "nick", None),
                getattr(member, "name", None),
            ):
                if _normalize_name_key(candidate_name) == target_key:
                    return str(getattr(member, "id", "") or "").strip() or None
    return None


# ─── VRChat status collection ─────────────────────────────────────────────

async def _resolve_vrchat_status(vrc_id: str):
    cache_entry = getattr(app_state, "user_online_cache", {}).get(vrc_id) or {}
    cached_online = bool(cache_entry.get("online", False))
    reason = cache_entry.get("reason")
    updated_at_ts = cache_entry.get("updated_at")

    pipeline_entry = getattr(app_state, "vrc_pipeline_friend_presence", {}).get(vrc_id) or {}
    platform = str(pipeline_entry.get("platform", "")).strip() or None

    vrc_status_hint = None

    if not cache_entry:
        try:
            from services.vrchat.vrchat_presence import get_vrchat_user_status
            is_online, _, source_or_status = await get_vrchat_user_status(vrchat_user_id=vrc_id)
            cached_online = bool(is_online) or cached_online
            if source_or_status and source_or_status not in ("pipeline", "friend_presence"):
                vrc_status_hint = str(source_or_status).lower().strip()
            
            refreshed = getattr(app_state, "user_online_cache", {}).get(vrc_id) or {}
            reason = refreshed.get("reason") or reason
            updated_at_ts = refreshed.get("updated_at") or updated_at_ts
        except Exception as exc:
            log.debug("[autosave] vrc fallback failed %s: %s", vrc_id, exc)

    if cached_online:
        if vrc_status_hint in ("join me", "joinme"):
            vrc_status = "joinme"
        elif vrc_status_hint in ("busy", "ask me", "askme"):
            vrc_status = "busy"
        else:
            vrc_status = "online"
    else:
        vrc_status = "offline"

    last_seen = None
    if updated_at_ts:
        try:
            last_seen = datetime.fromtimestamp(float(updated_at_ts), tz=timezone.utc).isoformat()
        except Exception:
            pass

    return vrc_status, platform, last_seen, reason


# ─── Dashboard sync pass ──────────────────────────────────────────────────

_last_presence_snapshot = None

async def _sync_to_dashboard():
    global _last_presence_snapshot
    sync = getattr(app_state, "dashboard_sync", None)
    if sync is None:
        return

    bot = getattr(app_state, "bot", None)
    vrc_to_discord = _build_vrc_to_discord_map()

    # leaderboard sync
    try:
        await sync.sync_leaderboard(leaderboard_data)
    except Exception as exc:
        log.warning("[autosave] leaderboard sync error: %s", exc)

    # offender sync
    try:
        offenders_list = []
        for user_id, data in repeat_offenders.items():
            warn = _safe_int(data.get("warn"))
            kick = _safe_int(data.get("kick"))
            ban = _safe_int(data.get("ban"))
            offenders_list.append({
                "vrchat_id": str(user_id) if str(user_id).startswith("usr_") else None,
                "discord_id": str(user_id) if not str(user_id).startswith("usr_") else None,
                "target_name": _safe_str(data.get("name")),
                "vrchat_username": _safe_str(data.get("name")),
                "warns": warn, "kicks": kick, "bans": ban,
                "is_repeat_offender": warn >= 3 or kick >= 2 or ban >= 1,
            })
        await sync.sync_offenders(offenders_list)
    except Exception as exc:
        log.warning("[autosave] offenders sync error: %s", exc)

    # presence sync
    try:
        statuses = await _collect_presence_snapshot(bot, vrc_to_discord)
        online_count = sum(1 for s in statuses if (s.get("vrchat_status") or "offline") != "offline")
        snapshot_key = f"{len(statuses)}:{online_count}"
        
        if snapshot_key != _last_presence_snapshot:
            log.info("[autosave] presence snapshot total=%s online=%s", len(statuses), online_count)
            _last_presence_snapshot = snapshot_key

        await sync.sync_vrchat_statuses(statuses)
    except Exception as exc:
        log.warning("[autosave] presence sync error: %s", exc)


async def _collect_presence_snapshot(bot, vrc_to_discord):
    staff_section = leaderboard_data.get("staff") or {}
    archive_section = leaderboard_data.get("archive") or {}

    def _row(vrc_id: str) -> dict:
        return staff_section.get(vrc_id) or archive_section.get(vrc_id) or {}

    async def _one(vrc_id: str, discord_id: str | None):
        vrc_status, platform, last_seen, reason = await _resolve_vrchat_status(vrc_id)
        discord_status, discord_avatar = (None, None)
        if discord_id:
            discord_status, discord_avatar = await _get_discord_presence(bot, discord_id)
        
        row = _row(vrc_id)
        return {
            "vrchat_id": vrc_id,
            "discord_id": discord_id,
            "vrchat_status": vrc_status,
            "discord_status": discord_status,
            "discord_avatar_url": discord_avatar,
            "vrchat_avatar_url": row.get("vrchat_avatar_url"),
            "discord_username": row.get("discord_username"),
            "vrchat_username": row.get("vrchat_username"),
            "platform": platform,
            "last_seen": last_seen,
            "status_last_source": reason or "autosave",
        }

    every_vrc_id: set[str] = set()
    every_vrc_id.update(vrc_to_discord.keys())
    if isinstance(staff_section, dict):
        for vid in staff_section.keys():
            if vid: every_vrc_id.add(str(vid).strip())

    tasks = []
    for vrc_id in every_vrc_id:
        discord_id = vrc_to_discord.get(vrc_id)
        if not discord_id:
            entry = _row(vrc_id)
            vrc_name = entry.get("vrchat_username") or entry.get("name")
            discord_id = _resolve_discord_id_via_guild(bot, vrc_name, vrc_id)
            if discord_id:
                vrc_to_discord[vrc_id] = discord_id
                if isinstance(entry, dict) and not entry.get("discord_id"):
                    entry["discord_id"] = discord_id
                    app_state.leaderboard_dirty = True
        
        tasks.append(_one(vrc_id, discord_id))

    return await asyncio.gather(*tasks) if tasks else []


async def _run_saves():
    await asyncio.to_thread(save_leaderboard_data)
    await asyncio.to_thread(save_repeat_offenders)


async def autosave_if_dirty():
    await _sync_to_dashboard()

    if not getattr(app_state, "leaderboard_dirty", False):
        return

    lock = getattr(app_state, "leaderboard_lock", None)
    if lock:
        async with lock:
            if getattr(app_state, "leaderboard_dirty", False):
                await _run_saves()
                app_state.leaderboard_dirty = False
                log.info("saved leaderboard + offenders")
