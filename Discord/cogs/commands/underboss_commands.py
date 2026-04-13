import discord
from discord.ext import commands
from discord import app_commands

from core.embeds import success_embed, warning_embed
from core.utils import respond

from .permissions import check_level, LEVEL_UNDERBOSS


class UnderbossCommands(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ============================================================
    # TEST COMMAND
    # ============================================================

    @commands.hybrid_command(
        name="underboss_ping",
        description="Test command for underboss rank",
    )
    async def underboss_ping(self, ctx: commands.Context) -> None:
        if not await check_level(ctx, LEVEL_UNDERBOSS):
            await respond(
                ctx,
                embed=warning_embed(
                    "Permission Denied",
                    "You do not have permission to use this command.",
                ),
                ephemeral=True,
            )
            return

        await respond(
            ctx,
            embed=success_embed(
                "Underboss Command Works",
                "Underboss commands file is loaded correctly.",
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UnderbossCommands(bot))
