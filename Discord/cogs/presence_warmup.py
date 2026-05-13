from __future__ import annotations

import asyncio
import logging
from typing import Iterable, Optional

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("presence_warmup")


# Module-level dict used as the source of truth for "fresh" Discord
# presence. Keyed by `int(discord_id)`. Values are lower-case strings
# matching what the dashboard expects: "online" | "idle" | "dnd" |
# "offline".
#
# This lives at module level (not on the cog) so:
#   • The autosave code can `from cogs.presence_warmup import
#     PRESENCE_CACHE` without holding a cog reference.
#   • Survives cog reloads in dev.
#
# Memory footprint: ~80 bytes per staff member. Even with 100k+ Discord
# server members this stays under 10 MB.
PRESENCE_CACHE: dict[int, str] = {}


def _normalize_status(raw: object) -> str:
    """Map discord.py's `Status` enum (or any string-ish value) to the
    canonical lowercase string the dashboard's Pydantic model accepts.
    `do_not_disturb` becomes `dnd`. Unknown values fall back to
    `offline` rather than raising — autosave never wants to throw."""
    if raw is None:
        return "offline"
    s = str(raw).lower().strip()
    if s in ("do_not_disturb", "dnd"):
        return "dnd"
    if s in ("online", "idle", "offline", "invisible"):
        # Discord exposes "invisible" only to the user themselves;
        # other clients always see them as offline.
        return "offline" if s == "invisible" else s
    return "offline"


def get_cached_status(discord_id: object) -> Optional[str]:
    """Public helper for the autosave code. Returns None if the cache
    has no entry — the caller should then fall back to `member.status`
    so we never *worsen* a working code path with stale data."""
    if discord_id is None:
        return None
    try:
        return PRESENCE_CACHE.get(int(discord_id))
    except (TypeError, ValueError):
        return None


class PresenceWarmup(commands.Cog):
    """Forces full guild member chunks on every gateway READY *and*
    maintains a live presence cache via `on_presence_update`. See
    module docstring for the full design rationale."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Track which guilds were chunked this session so reconnect
        # storms don't spam the gateway with redundant Guild Members
        # Request opcodes. discord.py also dedupes internally; this
        # just makes our stdout less noisy.
        self._chunked_ids: set[int] = set()

    # ───── READY-time chunking ─────────────────────────────────────

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self._chunk_all_guilds()
        # After chunk, seed PRESENCE_CACHE from whatever Discord *did*
        # give us. Members who weren't in the chunk response keep
        # their cache entry empty until the first PRESENCE_UPDATE
        # arrives — at which point the cache fills lazily.
        self._seed_cache_from_member_cache()

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self._chunk_one(guild)

    async def _chunk_all_guilds(self) -> None:
        guilds: Iterable[discord.Guild] = self.bot.guilds or ()
        for guild in guilds:
            await self._chunk_one(guild)
        log.info(
            "[presence_warmup] all guilds chunked — autosave will now stamp Discord presence"
        )

    async def _chunk_one(self, guild: discord.Guild) -> None:
        if guild.id in self._chunked_ids:
            return
        try:
            await asyncio.wait_for(guild.chunk(cache=True), timeout=30.0)
        except asyncio.TimeoutError:
            log.warning(
                "[presence_warmup] guild=%s chunk timed out — will retry on next on_ready",
                guild.name,
            )
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("[presence_warmup] guild=%s chunk failed: %s", guild.name, exc)
            return

        non_offline = sum(
            1 for m in guild.members if m.status != discord.Status.offline
        )
        log.info(
            "[presence_warmup] guild=%s chunked members=%d presences_non_offline=%d",
            guild.name,
            guild.member_count or 0,
            non_offline,
        )
        if non_offline == 0 and (guild.member_count or 0) > 0:
            log.warning(
                "[presence_warmup] guild=%s chunked but ZERO non-offline presences — verify Privileged Presence Intent in Developer Portal",
                guild.name,
            )
        self._chunked_ids.add(guild.id)

    def _seed_cache_from_member_cache(self) -> None:
        """Walk every cached member across every guild and seed
        PRESENCE_CACHE with whatever Discord pushed at chunk time.
        Members the chunk skipped (offline at chunk time) simply have
        no entry yet; on_presence_update fills them in lazily."""
        seeded = 0
        for guild in self.bot.guilds:
            for member in guild.members:
                if member.status == discord.Status.offline:
                    # Don't seed offline — let on_presence_update
                    # provide authoritative data later. Seeding offline
                    # here would override the previous-session cache
                    # entry if this is a reconnect, masking real state.
                    continue
                PRESENCE_CACHE[member.id] = _normalize_status(member.status)
                seeded += 1
        log.info("[presence_warmup] seeded %d non-offline members into PRESENCE_CACHE", seeded)

    # ───── Live updates via gateway events ─────────────────────────

    @commands.Cog.listener()
    async def on_presence_update(
        self, before: discord.Member, after: discord.Member
    ) -> None:
        """Fires whenever ANY member in ANY of the bot's guilds changes
        presence. We don't filter to staff IDs here — the cache is
        cheap and autosave only reads entries it asks for."""
        PRESENCE_CACHE[after.id] = _normalize_status(after.status)

    # ───── Manual force-refresh (Owner-only slash command) ────────

    @app_commands.command(
        name="refresh-presence",
        description="(Owner) Force-refresh staff Discord presence on the dashboard.",
    )
    async def refresh_presence(self, interaction: discord.Interaction) -> None:
        # Defer immediately — chunking can take ~10s on a busy guild.
        await interaction.response.defer(ephemeral=True, thinking=True)

        # Owner-check is intentionally inline — the staff-roles cog
        # check imports config we don't want to depend on here.
        if interaction.user.id != self.bot.owner_id:
            await interaction.followup.send("Owner-only.", ephemeral=True)
            return

        # Re-chunk every guild, then re-seed the cache so any stale
        # offline entries from the previous session get refreshed.
        self._chunked_ids.clear()
        await self._chunk_all_guilds()
        self._seed_cache_from_member_cache()

        size = len(PRESENCE_CACHE)
        await interaction.followup.send(
            f"Done. Forced presence refresh across {len(self.bot.guilds)} guild(s). "
            f"Cache now has {size} non-offline entries. Next autosave (≤30s) will push fresh data.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PresenceWarmup(bot))
