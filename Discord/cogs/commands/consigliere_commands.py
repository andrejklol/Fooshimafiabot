import asyncio
import re
import discord
from discord import app_commands
from discord.ext import commands

from core.config import GUILD_ID
from core.embeds import success_embed, warning_embed
from core.utils import respond

from services.leaderboard.processors import sync_all_vrc_staff_into_leaderboard

from .permissions import check_level, LEVEL_CONSIGLIERE

_DURATION_RE = re.compile(r"^(\d+)(m|h|d|w)$", re.IGNORECASE)

def parse_ban_duration(duration: str | None) -> int | None:
    if not duration:
        return None

    text = str(duration).strip().lower()
    if text in {"perm", "permanent", "forever"}:
        return None

    match = _DURATION_RE.match(text)
    if not match:
        raise ValueError("Use `perm`, `30m`, `12h`, `7d`, or `2w`.")

    amount = int(match.group(1))
    unit = match.group(2).lower()

    multipliers = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
    return amount * multipliers[unit]


class ConsigliereCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(
        name="syncvrc",
        description="Resync the VRChat member and staff cache with the leaderboard",
    )
    async def syncvrc(self, ctx: commands.Context):
        if not await check_level(ctx, LEVEL_CONSIGLIERE):
            return

        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)

        await sync_all_vrc_staff_into_leaderboard(self.bot)

        await respond(
            ctx,
            embed=success_embed("VRChat Members Refreshed", "Staff member cache has been updated."),
            ephemeral=True,
        )

    @commands.hybrid_command(
        name="ban",
        description="Ban a user from the server — temporarily (30m/12h/7d/2w) or permanently",
    )
    @app_commands.describe(
        user="User or ID to ban",
        duration="perm, 30m, 12h, 7d, or 2w",
        reason="Reason for the ban",
    )
    async def ban(
        self,
        ctx: commands.Context,
        user: discord.User,
        duration: str = "perm",
        *,
        reason: str = "No reason provided",
    ):
        if not await check_level(ctx, LEVEL_CONSIGLIERE):
            return

        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)

        try:
            duration_seconds = parse_ban_duration(duration)
        except ValueError as exc:
            await respond(ctx, embed=warning_embed("Invalid Duration", str(exc)), ephemeral=True)
            return

        try:
            await ctx.guild.ban(user, reason=reason, delete_message_seconds=0)
        except Exception as exc:
            await respond(
                ctx,
                embed=warning_embed("Ban Failed", f"Could not ban {user.name}: {str(exc)[:150]}"),
                ephemeral=True,
            )
            return

        if duration_seconds:
            async def unban_later():
                await asyncio.sleep(duration_seconds)
                try:
                    await ctx.guild.unban(user, reason=f"Temporary ban expired: {duration}")
                except (discord.NotFound, discord.Forbidden):
                    pass

            self.bot.loop.create_task(unban_later())

        title = "User Temporarily Banned" if duration_seconds else "User Banned"
        dur_display = duration if duration_seconds else "Permanent"

        await respond(
            ctx,
            embed=success_embed(
                title,
                f"**User:** {user.mention} ({user.id})\n**Duration:** {dur_display}\n**Reason:** {reason}",
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ConsigliereCommands(bot))
