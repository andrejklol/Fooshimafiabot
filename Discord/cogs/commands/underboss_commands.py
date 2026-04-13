import discord from discord.ext import commands from discord import
app_commands

from core.embeds import success_embed from core.utils import respond

class UnderbossCommands(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot


    # ============================================================
    # PLACEHOLDER
    # ============================================================

    # Currently no underboss-only commands.
    # File kept for future expansion.


    @commands.hybrid_command(
        name="underboss_ping",
        description="Test command for underboss rank",
    )
    async def underboss_ping(self, ctx):

        await respond(
            ctx,
            embed=success_embed(
                "Underboss Command Works",
                "Underboss commands file is loaded correctly.",
            ),
            ephemeral=True,
        )

async def setup(bot):

    await bot.add_cog(UnderbossCommands(bot))
