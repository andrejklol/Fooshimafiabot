import discord
from discord.ext import commands
from discord import app_commands

from core.embeds import success_embed
from core.utils import respond


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
    async def underboss_ping(self, ctx: commands.Context):

        await respond(
            ctx,
            embed=success_embed(
                "Underboss Command Works",
                "Underboss commands file is loaded correctly.",
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(UnderbossCommands(bot))
