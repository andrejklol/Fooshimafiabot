import discord
from discord import app_commands
from discord.ext import commands

from core.config import GUILD_ID, MAX_HISTORY_LOAD
from core.embeds import owner_embed, success_embed, warning_embed
from core.utils import (
    format_remaining_cooldown,
    respond,
    vrchat_cooldown_active,
)

from services.offenders.storage import reset_repeat_offenders
from services.leaderboard.service import (
    load_full_history,
    reset_leaderboard_data,
)

from .permissions import check_level, LEVEL_GODFOOSHI


class ConfirmResetView(discord.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=60)
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the command user can confirm this reset.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Confirm Reset", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True

        reset_leaderboard_data()
        reset_repeat_offenders()

        await interaction.response.edit_message(
            embed=owner_embed(
                "Data Reset Complete",
                "Leaderboard and repeat offender data were successfully cleared.",
            ),
            view=None,
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            embed=warning_embed("Reset Cancelled", "No data was changed."),
            view=None,
        )


class GodfooshiCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(
        name="sync",
        description="Force-sync all slash commands with Discord",
    )
    @app_commands.describe(
        clear_global="Set to True to clear old global ghost entries from Discord cache"
    )
    async def sync(self, ctx: commands.Context, clear_global: bool = False):
        if not await check_level(ctx, LEVEL_GODFOOSHI):
            return

        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)

        log_notes = []
        target_guild = discord.Object(id=GUILD_ID)

        if clear_global:
            self.bot.tree.clear_commands(guild=None)
            await self.bot.tree.sync(guild=None)
            log_notes.append("Global tree cleared and unlinked.")

        try:
            self.bot.tree.copy_global_to(guild=target_guild)
            synced = await self.bot.tree.sync(guild=target_guild)
            log_notes.append(f"Successfully synced {len(synced)} server hierarchy commands.")
        except Exception as e:
            log_notes.append(f"❌ Server synchronization failed: {str(e)}")

        result = "\n".join(f"• {note}" for note in log_notes)

        await respond(
            ctx,
            embed=success_embed("Commands Synced", result),
            ephemeral=True,
        )

    @commands.hybrid_command(
        name="history",
        description="Load past VRChat audit logs into the leaderboard",
    )
    @app_commands.describe(
        amount="How many past logs to scan",
        rebuild="Rebuild leaderboard from logs",
        monthly_only="Only rebuild monthly leaderboard",
    )
    async def history(
        self,
        ctx: commands.Context,
        amount: int = 5000,
        rebuild: bool = True,
        monthly_only: bool = False,
    ):
        if not await check_level(ctx, LEVEL_GODFOOSHI):
            return

        if ctx.interaction:
            await ctx.interaction.response.defer(ephemeral=True)

        if amount <= 0:
            await respond(
                ctx,
                embed=warning_embed("Invalid Amount", "Amount must be greater than 0."),
                ephemeral=True,
            )
            return

        amount = min(amount, MAX_HISTORY_LOAD)

        if vrchat_cooldown_active():
            await respond(
                ctx,
                embed=warning_embed(
                    "VRChat Cooldown Active",
                    f"The API is currently on cooldown. {format_remaining_cooldown()}",
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
                f"Fetched **{result.get('fetched', 0)}** entries — "
                f"**{result.get('counted_total', 0)}** processed "
                f"({result.get('warns', 0)} warns, {result.get('kicks', 0)} kicks, "
                f"{result.get('bans', 0)} bans, {result.get('invite_accepts', 0)} invite accepts).",
            ),
            ephemeral=True,
        )

    @commands.hybrid_command(
        name="wipe",
        description="Erase all leaderboard and repeat offender data (irreversible)",
    )
    async def wipe(self, ctx: commands.Context):
        if not await check_level(ctx, LEVEL_GODFOOSHI):
            return

        view = ConfirmResetView(author_id=ctx.author.id)

        embed = warning_embed(
            "Confirm Data Wipe",
            "This will permanently erase all leaderboard and repeat offender data.\n\n"
            "This action cannot be undone.",
        )

        await respond(ctx, embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(GodfooshiCommands(bot))