import os

import discord
from discord.ext import commands

from core.cache import app_state
from core.config import OWNER_USER_ID
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


class General(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ============================================================
    # HELPERS
    # ============================================================

    async def _send_general_error(
            self,
            ctx,
            user_title: str,
            log_title: str,
            exc: Exception,
    ):
        embed = warning_embed(user_title, str(exc))
        await respond(ctx, embed=embed, ephemeral=True)
        await send_error_log(log_title, exc)

    # ============================================================
    # BASIC UTILITY COMMANDS
    # ============================================================

    @commands.hybrid_command(name="ping", description="Check bot latency")
    async def ping(self, ctx):
        latency = round(self.bot.latency * 1000)

        embed = info_embed(
            "Pong",
            f"Latency: `{latency} ms`",
        )

        await respond(ctx, embed=embed)

    # ============================================================
    # STATUS / HEALTH COMMANDS
    # ============================================================

    @commands.hybrid_command(
        name="vrcstatus",
        description="Show VRChat + bot health status",
    )
    async def vrcstatus(self, ctx):

        embed = info_embed("System Health")

        # --------------------------------------------------------
        # VRChat status
        # --------------------------------------------------------

        if app_state.vrc_client and app_state.vrc_auth_api:

            if vrchat_cooldown_active():

                embed.add_field(
                    name="VRChat",
                    value=f"Cooldown active\n`{format_remaining_cooldown()}`",
                    inline=False,
                )

            else:

                try:

                    user = await run_blocking(
                        app_state.vrc_auth_api.get_current_user
                    )

                    embed.add_field(
                        name="VRChat",
                        value=f"Connected as **{user.display_name}**",
                        inline=False,
                    )

                except Exception as exc:

                    embed.add_field(
                        name="VRChat",
                        value=f"API error\n`{str(exc)[:120]}`",
                        inline=False,
                    )

                    await send_error_log(
                        "VRChat Status Error",
                        exc,
                    )

        else:

            embed.add_field(
                name="VRChat",
                value="Not connected",
                inline=False,
            )

        # --------------------------------------------------------
        # Bot status
        # --------------------------------------------------------

        embed.add_field(
            name="API Cooldown",
            value=format_remaining_cooldown(),
            inline=True,
        )

        embed.add_field(
            name="Bot Ping",
            value=f"{round(self.bot.latency * 1000)} ms",
            inline=True,
        )

        embed.add_field(
            name="Startup Log Marker",
            value=format_dt(app_state.startup_timestamp),
            inline=False,
        )

        embed.add_field(
            name="Last Log Received",
            value=format_dt(app_state.last_log_received_at),
            inline=False,
        )

        # --------------------------------------------------------
        # Leaderboard status
        # --------------------------------------------------------

        staff_data = leaderboard_data.get("staff", {})

        total_warns = sum(s["warn"] for s in staff_data.values())
        total_kicks = sum(s["kick"] for s in staff_data.values())
        total_bans = sum(s["ban"] for s in staff_data.values())
        total_invites = sum(s["invite"] for s in staff_data.values())
        total_accepts = sum(s["invite_accept"] for s in staff_data.values())

        embed.add_field(
            name="Tracked Actions",
            value=(
                f"Warnings: {total_warns}\n"
                f"Kicks: {total_kicks}\n"
                f"Bans: {total_bans}\n"
                f"Invites: {total_invites}\n"
                f"Invite Accepts: {total_accepts}"
            ),
            inline=True,
        )

        embed.add_field(
            name="Activity Total",
            value=str(
                total_warns
                + total_kicks
                + total_bans
                + total_invites
                + total_accepts
            ),
            inline=True,
        )

        embed.add_field(
            name="Repeat Targets Cached",
            value=str(len(app_state.repeat_offender_actions)),
            inline=True,
        )

        embed.add_field(
            name="VRC Members Cached",
            value=str(len(app_state.vrc_group_member_roles)),
            inline=True,
        )

        if app_state.last_api_error:

            embed.add_field(
                name="Last API Error",
                value=app_state.last_api_error[:200],
                inline=False,
            )

        await respond(ctx, embed=embed, ephemeral=True)

    # ============================================================
    # HELP COMMAND
    # ============================================================

    @commands.hybrid_command(
        name="help",
        description="Show all commands",
    )
    async def help_command(self, ctx):

        try:

            embed = info_embed("Bot Commands")

            embed.add_field(
                name="Utility",
                value=(
                    "`/ping`\n"
                    "`/vrcstatus`"
                ),
                inline=False,
            )

            embed.add_field(
                name="Staff Tracking",
                value=(
                    "`/leaderboard`\n"
                    "`/staffrecord`\n"
                    "`/repeatstats`"
                ),
                inline=False,
            )

            if ctx.author.id == OWNER_USER_ID:

                embed.add_field(
                    name="Debug",
                    value=(
                        "`/simulaterepeatalert`\n"
                        "`/staffstatus`"
                    ),
                    inline=False,
                )

                embed.add_field(
                    name="Owner",
                    value=(
                        "`/refreshvrcmembers`\n"
                        "`/loadvrchistory`\n"
                        "`/resetvrcdata`\n"
                        "`/synccommands`"
                    ),
                    inline=False,
                )

            await respond(ctx, embed=embed, ephemeral=True)

        except Exception as exc:

            await self._send_general_error(
                ctx,
                "Help Command Failed",
                "Help Command Error",
                exc,
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(General(bot))
