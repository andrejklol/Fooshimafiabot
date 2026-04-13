from typing import Optional import discord from discord import
app_commands from discord.ext import commands

from core.cache import app_state from core.config import (
ERROR_LOG_CHANNEL_ID, GUILD_ID, MAX_HISTORY_LOAD, ) from core.embeds
import owner_embed, success_embed, warning_embed from core.error_embed
import send_error_embed from core.utils import (
format_remaining_cooldown, respond, send_error_log,
vrchat_cooldown_active, ) from services.offenders import
send_repeat_alert from services.leaderboard.service import (
load_full_history, reset_leaderboard_data,
reset_monthly_leaderboard_data, ) from services.offenders.storage import
reset_repeat_offenders

============================================================

SYNC HELPER

============================================================

async def perform_command_sync(bot: commands.Bot, clear_guild: bool =
False) -> str: if not GUILD_ID: synced = await bot.tree.sync() return
f”Globally synced {len(synced)} commands.”

    guild = discord.Object(id=GUILD_ID)

    if clear_guild:
        bot.tree.clear_commands(guild=guild)
        await bot.tree.sync(guild=guild)

    bot.tree.copy_global_to(guild=guild)
    synced = await bot.tree.sync(guild=guild)
    return f"Guild synced {len(synced)} commands."

class OwnerCommands(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot


    @commands.hybrid_command(
        name="resetvrcdata",
        description="Reset leaderboard and repeat offender data",
    )
    async def resetvrcdata(self, ctx: commands.Context) -> None:

        reset_leaderboard_data()
        reset_repeat_offenders()

        await respond(
            ctx,
            embed=owner_embed(
                "VRChat Data Reset",
                "Leaderboard and repeat offender data have been reset.",
            ),
            ephemeral=True,
        )


    @commands.hybrid_command(
        name="loadvrchistory",
        description="Load past VRChat audit logs",
    )
    async def loadvrchistory(
        self,
        ctx: commands.Context,
        amount: int = 5000,
        rebuild: bool = True,
        monthly_only: bool = False,
    ) -> None:

        if vrchat_cooldown_active():
            await respond(
                ctx,
                embed=warning_embed(
                    "VRChat Cooldown Active",
                    f"Try again later. {format_remaining_cooldown()}",
                ),
                ephemeral=True,
            )
            return

        result = await load_full_history(
            limit=amount,
            rebuild=rebuild,
            monthly_only=monthly_only,
        )

        await respond(
            ctx,
            embed=success_embed(
                "History Loaded",
                f"Loaded {result} entries.",
            ),
            ephemeral=True,
        )


    @commands.hybrid_command(
        name="synccommands",
        description="Sync slash commands",
    )
    async def synccommands(self, ctx: commands.Context, clear_guild: bool = False):

        result = await perform_command_sync(self.bot, clear_guild=clear_guild)

        await respond(
            ctx,
            embed=success_embed("Commands Synced", result),
            ephemeral=True,
        )


    @commands.hybrid_command(
        name="simulaterepeatalert",
        description="Simulate repeat offender alert",
    )
    async def simulaterepeatalert(self, ctx):

        await send_repeat_alert(
            pretty_name="TestUser",
            target_id="usr_test_repeat_user",
            triggered=[("warn", 3, 7, 3)],
            highest_action="warn",
        )

        await respond(
            ctx,
            embed=success_embed(
                "Simulation Complete",
                "Repeat alert triggered.",
            ),
            ephemeral=True,
        )


    @commands.hybrid_command(
        name="reset_monthly_leaderboard",
        description="Reset monthly leaderboard only",
    )
    async def reset_monthly_leaderboard(self, ctx):

        async with app_state.leaderboard_lock:
            reset_monthly_leaderboard_data()

        await respond(
            ctx,
            embed=owner_embed(
                "Monthly Reset",
                "Monthly leaderboard cleared.",
            ),
            ephemeral=True,
        )


    @commands.hybrid_command(
        name="testerror",
        description="Send test error",
    )
    async def testerror(self, ctx):

        try:
            1/0

        except Exception as exc:

            await send_error_embed(
                self.bot,
                ERROR_LOG_CHANNEL_ID,
                title="Test Error",
                description=str(exc),
                trace_id="testerror",
                level="error",
            )

        await respond(
            ctx,
            embed=success_embed(
                "Test Error Sent",
                "Check error log channel.",
            ),
            ephemeral=True,
        )

async def setup(bot: commands.Bot): await
bot.add_cog(OwnerCommands(bot))
