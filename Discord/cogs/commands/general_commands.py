import discord
from discord import app_commands
from discord.ext import commands

from core.embeds import info_embed
from core.utils import respond

WEBSITE_URL = "https://fooshimafia.net/"
STAFF_DASHBOARD_URL = "https://fooshimafia.net/staff"


class GeneralCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(
        name="menu",
        description="Browse all bot commands organized by rank",
    )
    async def menu(self, ctx: commands.Context):
        embed = info_embed(
            "Fooshi Mafia — Command Menu",
            "Commands are locked by rank. Use the Staff Dashboard for detailed logs.",
        )

        embed.add_field(
            name="📁 General",
            value=(
                "`/menu` — Show this command list\n"
                "`/ping` — Check bot response time\n"
                "`/links` — Website and staff portal links"
            ),
            inline=False,
        )

        embed.add_field(
            name="🔫 Soldier+",
            value=(
                "`/status` — VRChat API and bot health\n"
                "`/warn` — Issue a formal warning to a member"
            ),
            inline=False,
        )

        embed.add_field(
            name="👔 Capo+",
            value=(
                "`/purge` — Bulk delete messages from a channel\n"
                "`/kick` — Remove a member from the server"
            ),
            inline=False,
        )

        embed.add_field(
            name="⚖️ Consigliere+",
            value=(
                "`/syncvrc` — Resync the VRChat member and staff cache\n"
                "`/ban` — Temporarily or permanently ban a user"
            ),
            inline=False,
        )

        embed.add_field(
            name="💼 Underboss+",
            value=(
                "`/cooldown` — Set a message delay for the current channel\n"
                "`/nickname` — Change a member's nickname\n"
                "`/giveaway` — Start, end, or reroll a giveaway"
            ),
            inline=False,
        )

        embed.add_field(
            name="👑 Godfooshi",
            value=(
                "`/sync` — Force-sync slash commands with Discord\n"
                "`/history` — Load past VRChat audit logs into the leaderboard\n"
                "`/wipe` — Erase all leaderboard and offender data"
            ),
            inline=False,
        )

        embed.add_field(
            name="🌐 Resources",
            value=f"[Website]({WEBSITE_URL}) | [Staff Dashboard]({STAFF_DASHBOARD_URL})",
            inline=False,
        )

        await respond(ctx, embed=embed, ephemeral=True)

    @commands.hybrid_command(
        name="ping",
        description="Check the bot's current response time",
    )
    async def ping(self, ctx: commands.Context):
        latency = round(self.bot.latency * 1000)
        embed = info_embed("Pong!", f"Response time: `{latency}ms`")
        await respond(ctx, embed=embed, ephemeral=True)

    @commands.hybrid_command(
        name="links",
        description="Get links to the Fooshi Mafia website and staff portal",
    )
    async def links(self, ctx: commands.Context):
        embed = info_embed(
            "Fooshi Mafia Links",
            f"**Main Site:** {WEBSITE_URL}\n**Staff Portal:** {STAFF_DASHBOARD_URL}",
        )
        await respond(ctx, embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(GeneralCommands(bot))