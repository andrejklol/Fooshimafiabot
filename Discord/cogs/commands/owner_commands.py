import discord
from discord import app_commands
from discord.ext import commands

from core.cache import app_state
from core.config import (
    ERROR_LOG_CHANNEL_ID,
    GUILD_ID,
    MAX_HISTORY_LOAD,
)
from core.embeds import owner_embed, success_embed, warning_embed
from core.error_embed import send_error_embed
from core.utils import (
    format_remaining_cooldown,
    respond,
    vrchat_cooldown_active,
)
from services.offenders import send_repeat_alert
from services.leaderboard.service import (
    load_full_history,
    reset_leaderboard_data,
    reset_monthly_leaderboard_data,
)
from services.offenders.storage import reset_repeat_offenders


# ============================================================
# SYNC HELPER
# ============================================================

async def perform_command_sync(bot: commands.Bot, clear_guild: bool = False) -> str:
    if not GUILD_ID:
        synced = await bot.tree.sync()
        return f"Globally synced {len(synced)} commands."

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
    @app_commands.describe(
        amount="How many past logs to scan",
        rebuild="Reset and rebuild leaderboard from fetched history",
        monthly_only="Rebuild only this month's leaderboard counters",
    )
    async def loadvrchistory(
        self,
        ctx: commands.Context,
        amount: int = 5000,
        rebuild: bool = True,
        monthly_only: bool = False,
    ) -> None:
        if amount <= 0:
            await respond(
                ctx,
                embed=warning_embed(
                    "Invalid Amount",
                    "Amount must be greater than 0.",
                ),
                ephemeral=True,
            )
            return

        amount = min(amount, MAX_HISTORY_LOAD)

        if vrchat_cooldown_active():
            await respond(
                ctx,
                embed=warning_embed(
                    "VRChat Cooldown Active",
                    f"Try again later.\n`{format_remaining_cooldown()}`",
                ),
                ephemeral=True,
            )
            return

        try:
            result = await load_full_history(
                limit=amount,
                rebuild=rebuild,
                monthly_only=monthly_only,
            )

            await respond(
                ctx,
                embed=success_embed(
                    "History Load Complete",
                    f"Loaded history result: `{result}`",
                ),
                ephemeral=True,
            )

        except Exception as exc:
            await respond(
                ctx,
                embed=warning_embed(
                    "Load History Failed",
                    str(exc),
                ),
                ephemeral=True,
            )

    @commands.hybrid_command(
        name="synccommands",
        description="Sync slash commands",
    )
    @app_commands.describe(clear_guild="Clear current guild commands before syncing")
    async def synccommands(
        self,
        ctx: commands.Context,
        clear_guild: bool = False,
    ) -> None:
        try:
            result = await perform_command_sync(self.bot, clear_guild=clear_guild)

            await respond(
                ctx,
                embed=success_embed("Commands Synced", result),
                ephemeral=True,
            )

        except Exception as exc:
            await respond(
                ctx,
                embed=warning_embed("Sync Failed", str(exc)),
                ephemeral=True,
            )

    @commands.hybrid_command(
        name="simulaterepeatalert",
        description="Simulate repeat offender alert",
    )
    @app_commands.describe(action="Type of alert to simulate")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="warn", value="warn"),
            app_commands.Choice(name="kick", value="kick"),
            app_commands.Choice(name="ban", value="ban"),
        ]
    )
    async def simulaterepeatalert(
        self,
        ctx: commands.Context,
        action: app_commands.Choice[str],
    ) -> None:
        triggered_map = {
            "warn": [("warn", 3, 7, 3)],
            "kick": [("warn", 5, 7, 3), ("kick", 2, 30, 2)],
            "ban": [("warn", 6, 7, 3), ("kick", 3, 30, 2), ("ban", 1, 30, 1)],
        }

        try:
            await send_repeat_alert(
                pretty_name="TestUser",
                target_id="usr_test_repeat_user",
                triggered=triggered_map[action.value],
                highest_action=action.value,
            )

            await respond(
                ctx,
                embed=success_embed(
                    "Simulation Complete",
                    f"Repeat alert triggered for `{action.value}`.",
                ),
                ephemeral=True,
            )

        except Exception as exc:
            await respond(
                ctx,
                embed=warning_embed("Simulation Failed", str(exc)),
                ephemeral=True,
            )

    @commands.hybrid_command(
        name="reset_monthly_leaderboard",
        description="Reset monthly leaderboard only",
    )
    async def reset_monthly_leaderboard(self, ctx: commands.Context) -> None:
        try:
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

        except Exception as exc:
            await respond(
                ctx,
                embed=warning_embed("Monthly Reset Failed", str(exc)),
                ephemeral=True,
            )

    @commands.hybrid_command(
        name="testerror",
        description="Send test error",
    )
    async def testerror(self, ctx: commands.Context) -> None:
        try:
            1 / 0

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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OwnerCommands(bot))
