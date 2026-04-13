import discord from discord import app_commands from discord.ext import
commands

from core.embeds import info_embed, success_embed, warning_embed from
core.utils import respond, send_error_log from core.cache import
app_state

from services.leaderboard.queries import get_top_staff from
services.leaderboard.scoring import build_score_footer from
services.leaderboard.storage import leaderboard_data

LEVEL_CAPO = 2

class CapoCommands(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot


    # ============================================================
    # CLEAR
    # ============================================================

    @commands.hybrid_command(
        name="clear",
        description="Delete recent messages",
    )
    @app_commands.describe(amount="Messages to delete (max 100)")
    async def clear(self, ctx: commands.Context, amount: int):

        if amount <= 0:
            await respond(
                ctx,
                embed=warning_embed("Invalid Amount", "Amount must be above 0"),
                ephemeral=True,
            )
            return

        amount = min(amount, 100)

        deleted = await ctx.channel.purge(limit=amount)

        await respond(
            ctx,
            embed=success_embed(
                "Messages Deleted",
                f"Deleted {len(deleted)} messages",
            ),
            ephemeral=True,
        )


    # ============================================================
    # STAFF RECORD
    # ============================================================

    def _get_scope_data(self, scope="staff"):
        return leaderboard_data.get(scope, {})


    def _build_embed(self, name, record):

        embed = info_embed(f"Staff Record — {name}")

        embed.add_field(name="Warns", value=str(record.get("warn", 0)))
        embed.add_field(name="Kicks", value=str(record.get("kick", 0)))
        embed.add_field(name="Bans", value=str(record.get("ban", 0)))
        embed.add_field(name="Invites", value=str(record.get("invite", 0)))
        embed.add_field(name="Points", value=str(record.get("points", 0)))

        embed.set_footer(text=build_score_footer())

        return embed


    @commands.hybrid_command(
        name="staffrecord",
        description="Check staff moderation stats",
    )
    async def staffrecord(self, ctx, staff: str):

        data = self._get_scope_data()

        record = data.get(staff)

        if not record:
            await respond(
                ctx,
                embed=warning_embed(
                    "Not Found",
                    "No staff record found",
                ),
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
    async def repeatstats(self, ctx):

        embed = info_embed("Repeat Stats")

        embed.add_field(
            name="Tracked Users",
            value=str(len(app_state.repeat_offender_actions)),
        )

        embed.add_field(
            name="Alert Keys",
            value=str(len(app_state.repeat_alerted_keys)),
        )

        await respond(ctx, embed=embed, ephemeral=True)

async def setup(bot): await bot.add_cog(CapoCommands(bot))
