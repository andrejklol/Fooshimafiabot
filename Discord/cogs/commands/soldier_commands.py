from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from core.embeds import info_embed, success_embed, warning_embed
from core.utils import (
    format_dt,
    format_remaining_cooldown,
    respond,
    run_blocking,
    send_error_log,
    vrchat_cooldown_active,
)
from services.leaderboard.storage import leaderboard_data
from .permissions import check_level, LEVEL_SOLDIER


class SoldierCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(
        name="status",
        description="View VRChat API connection and bot health",
    )
    async def status(self, ctx: commands.Context):
        if not await check_level(ctx, LEVEL_SOLDIER):
            return

        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)

        from core.cache import app_state

        embed = info_embed("VRChat + Bot Health")

        if app_state.vrc_client and app_state.vrc_auth_api:
            if vrchat_cooldown_active():
                embed.add_field(
                    name="VRChat API",
                    value=f"⚠️ Cooldown active\nEnds in: `{format_remaining_cooldown()}`",
                    inline=False,
                )
            else:
                try:
                    user = await run_blocking(app_state.vrc_auth_api.get_current_user)
                    embed.add_field(
                        name="VRChat API",
                        value=f"✅ Connected as **{user.display_name}**",
                        inline=False,
                    )
                except Exception as exc:
                    embed.add_field(
                        name="VRChat API",
                        value=f"❌ API error: `{str(exc)[:100]}`",
                        inline=False,
                    )
                    await send_error_log("VRChat Status Fetch Error", exc)
        else:
            embed.add_field(name="VRChat API", value="⚪ Not initialized", inline=False)

        embed.add_field(name="Bot Ping", value=f"{round(self.bot.latency * 1000)}ms", inline=True)
        embed.add_field(name="VRC Members", value=str(len(getattr(app_state, "vrc_group_member_roles", {}))), inline=True)
        embed.add_field(name="Bot Startup", value=format_dt(app_state.startup_timestamp), inline=False)

        if app_state.last_log_received_at:
            embed.add_field(name="Last Log Received", value=format_dt(app_state.last_log_received_at), inline=False)

        staff_data = leaderboard_data.get("staff", {})
        if isinstance(staff_data, dict):
            metrics = {"warn": 0, "kick": 0, "ban": 0, "invite": 0, "invite_accept": 0}
            for staff in staff_data.values():
                for key in metrics:
                    metrics[key] += int(staff.get(key, 0) or 0)

            embed.add_field(
                name="Total Tracked Actions",
                value=(
                    f"Warns: `{metrics['warn']}` | Kicks: `{metrics['kick']}`\n"
                    f"Bans: `{metrics['ban']}` | Invites: `{metrics['invite']}`\n"
                    f"Accepted Invites: `{metrics['invite_accept']}`"
                ),
                inline=False,
            )

        await respond(ctx, embed=embed, ephemeral=True)

    @commands.hybrid_command(
        name="warn",
        description="Issue a formal warning to a member and log it",
    )
    @app_commands.describe(user="The member to warn", reason="Reason for the warning")
    async def warn(
        self,
        ctx: commands.Context,
        user: discord.Member,
        *,
        reason: str = "No reason provided",
    ):
        if not await check_level(ctx, LEVEL_SOLDIER):
            return

        if user.id == ctx.author.id:
            return await respond(ctx, embed=warning_embed("Warning Failed", "You cannot warn yourself."), ephemeral=True)

        embed = success_embed(
            "User Warned",
            f"**User:** {user.mention} (`{user.id}`)\n**Reason:** {reason}\n**Moderator:** {ctx.author.mention}",
        )
        await respond(ctx, embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(SoldierCommands(bot))
