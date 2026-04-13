import discord
from discord import app_commands
from discord.ext import commands

from core.cache import app_state
from core.embeds import info_embed, success_embed, warning_embed
from core.utils import respond
from services.leaderboard.scoring import build_score_footer
from services.leaderboard.storage import leaderboard_data

from .permissions import check_level, LEVEL_CAPO


class CapoCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _get_scope_data(self, scope: str = "staff") -> dict:
        data = leaderboard_data.get(scope, {})
        return data if isinstance(data, dict) else {}

    def _build_embed(self, name: str, record: dict) -> discord.Embed:
        embed = info_embed(f"Staff Record — {name}")

        embed.add_field(name="Warns", value=str(record.get("warn", 0)), inline=True)
        embed.add_field(name="Kicks", value=str(record.get("kick", 0)), inline=True)
        embed.add_field(name="Bans", value=str(record.get("ban", 0)), inline=True)
        embed.add_field(name="Invites", value=str(record.get("invite", 0)), inline=True)
        embed.add_field(name="Points", value=str(record.get("points", 0)), inline=True)

        embed.set_footer(text=build_score_footer())
        return embed

    # ============================================================
    # CLEAR
    # ============================================================

    @commands.hybrid_command(
        name="clear",
        description="Delete recent messages",
    )
    @app_commands.describe(amount="Messages to delete (max 100)")
    async def clear(self, ctx: commands.Context, amount: int) -> None:
        if not await check_level(ctx, LEVEL_CAPO):
            await respond(
                ctx,
                embed=warning_embed(
                    "Permission Denied",
                    "You do not have permission to use this command.",
                ),
                ephemeral=True,
            )
            return

        if amount <= 0:
            await respond(
                ctx,
                embed=warning_embed("Invalid Amount", "Amount must be above 0."),
                ephemeral=True,
            )
            return

        if ctx.channel is None:
            await respond(
                ctx,
                embed=warning_embed(
                    "Clear Failed",
                    "This command must be used in a server channel.",
                ),
                ephemeral=True,
            )
            return

        amount = min(amount, 100)

        try:
            if getattr(ctx, "interaction", None) and not ctx.interaction.response.is_done():
                await ctx.interaction.response.defer(ephemeral=True)

            deleted = await ctx.channel.purge(limit=amount)

            if getattr(ctx, "interaction", None):
                await ctx.interaction.followup.send(
                    embed=success_embed(
                        "Messages Deleted",
                        f"Deleted {len(deleted)} messages.",
                    ),
                    ephemeral=True,
                )
            else:
                await respond(
                    ctx,
                    embed=success_embed(
                        "Messages Deleted",
                        f"Deleted {len(deleted)} messages.",
                    ),
                    ephemeral=True,
                )

        except Exception as exc:
            if getattr(ctx, "interaction", None):
                await ctx.interaction.followup.send(
                    embed=warning_embed("Clear Failed", str(exc)),
                    ephemeral=True,
                )
            else:
                await respond(
                    ctx,
                    embed=warning_embed("Clear Failed", str(exc)),
                    ephemeral=True,
                )

    # ============================================================
    # STAFF RECORD
    # ============================================================

    @commands.hybrid_command(
        name="staffrecord",
        description="Check staff moderation stats",
    )
    @app_commands.describe(staff="Leaderboard staff ID")
    async def staffrecord(self, ctx: commands.Context, staff: str) -> None:
        if not await check_level(ctx, LEVEL_CAPO):
            await respond(
                ctx,
                embed=warning_embed(
                    "Permission Denied",
                    "You do not have permission to use this command.",
                ),
                ephemeral=True,
            )
            return

        data = self._get_scope_data()
        record = data.get(staff)

        if not record:
            await respond(
                ctx,
                embed=warning_embed("Not Found", "No staff record found."),
                ephemeral=True,
            )
            return

        embed = self._build_embed(staff, record)
        await respond(ctx, embed=embed, ephemeral=True)

    # ============================================================
    # REPEAT STATS
    # ============================================================

    @commands.hybrid_command(
        name="repeatstats",
        description="Show repeat offender stats",
    )
    async def repeatstats(self, ctx: commands.Context) -> None:
        if not await check_level(ctx, LEVEL_CAPO):
            await respond(
                ctx,
                embed=warning_embed(
                    "Permission Denied",
                    "You do not have permission to use this command.",
                ),
                ephemeral=True,
            )
            return

        embed = info_embed("Repeat Stats")
        embed.add_field(
            name="Tracked Users",
            value=str(len(app_state.repeat_offender_actions)),
            inline=True,
        )
        embed.add_field(
            name="Alert Keys",
            value=str(len(app_state.repeat_alerted_keys)),
            inline=True,
        )

        await respond(ctx, embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CapoCommands(bot))
