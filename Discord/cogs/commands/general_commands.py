import discord
from discord.ext import commands
from discord import app_commands

from core.embeds import leaderboard_embed, warning_embed
from core.utils import respond

from services.leaderboard.storage import leaderboard_data
from services.leaderboard.scoring import build_score_footer

from .permissions import check_level, LEVEL_SOLDIER


_VALID_SCOPES = ["overall", "monthly"]


class GeneralCommands(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ============================================================
    # HELPERS
    # ============================================================

    def _get_scope_data(self, scope: str) -> dict:
        if scope == "monthly":
            data = leaderboard_data.get("monthly", {})
        else:
            data = leaderboard_data.get("staff", {})

        return data if isinstance(data, dict) else {}

    def _top(self, data: dict, key: str) -> list[dict]:
        return sorted(
            data.values(),
            key=lambda x: int(x.get(key, 0) or 0),
            reverse=True,
        )[:3]

    def _format(self, rows: list[dict], key: str) -> list[str]:
        medals = ["🥇", "🥈", "🥉"]
        lines: list[str] = []

        for i, row in enumerate(rows):
            name = row.get("name", "Unknown")
            value = row.get(key, 0)
            lines.append(f"{medals[i]} {name} — {value}")

        return lines

    # ============================================================
    # LEADERBOARD
    # ============================================================

    @commands.hybrid_command(
        name="leaderboard",
        description="Show moderation leaderboard",
    )
    @app_commands.describe(scope="overall or monthly")
    async def leaderboard(self, ctx: commands.Context, scope: str = "overall") -> None:
        if not await check_level(ctx, LEVEL_SOLDIER):
            await respond(
                ctx,
                embed=warning_embed(
                    "Permission Denied",
                    "You do not have permission to use this command.",
                ),
                ephemeral=True,
            )
            return

        scope = scope.lower().strip()

        if scope not in _VALID_SCOPES:
            await respond(
                ctx,
                embed=warning_embed(
                    "Invalid Scope",
                    "Use overall or monthly.",
                ),
                ephemeral=True,
            )
            return

        data = self._get_scope_data(scope)

        points = self._format(self._top(data, "points"), "points")
        warns = self._format(self._top(data, "warn"), "warn")
        kicks = self._format(self._top(data, "kick"), "kick")
        bans = self._format(self._top(data, "ban"), "ban")

        text = (
            "**Top Points**\n"
            + "\n".join(points)
            + "\n\n**Top Warns**\n"
            + "\n".join(warns)
            + "\n\n**Top Kicks**\n"
            + "\n".join(kicks)
            + "\n\n**Top Bans**\n"
            + "\n".join(bans)
        )

        embed = leaderboard_embed(
            f"Leaderboard — {scope.title()}",
            text,
        )
        embed.set_footer(text=build_score_footer())

        await respond(ctx, embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GeneralCommands(bot))
