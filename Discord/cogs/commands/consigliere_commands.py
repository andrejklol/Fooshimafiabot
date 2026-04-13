import discord from discord import app_commands from discord.ext import
commands

from core.embeds import info_embed, success_embed, warning_embed from
core.utils import respond, send_error_log

from services.leaderboard.storage import leaderboard_data from
services.vrchat_client import get_vrchat_user_status from core.config
import GUILD_ID, STAFF_ALERT_ORDER

class ConsigliereCommands(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot


    # ============================================================
    # STAFF STATUS
    # ============================================================

    def _chunk(self, text, limit=1900):

        if len(text) <= limit:
            return [text]

        parts = []
        current = ""

        for line in text.splitlines():

            if len(current) + len(line) > limit:

                parts.append(current)
                current = line

            else:

                current += "\n" + line

        parts.append(current)

        return parts


    @commands.hybrid_command(
        name="staffstatus",
        description="Show VRChat online status of staff",
    )
    async def staffstatus(self, ctx):

        guild = self.bot.get_guild(GUILD_ID)

        if not guild:

            await respond(
                ctx,
                embed=warning_embed(
                    "Guild Missing",
                    "Bot could not find guild",
                ),
                ephemeral=True,
            )

            return


        lines = []

        for action, groups in STAFF_ALERT_ORDER.items():

            lines.append(f"## {action}")

            for rank_name, members in groups:

                lines.append(f"**{rank_name}**")

                for entry in members:

                    member = guild.get_member(entry["discord_id"])

                    online, _, status = await get_vrchat_user_status(
                        vrchat_username=entry.get("vrchat_username"),
                        vrchat_user_id=entry.get("vrchat_user_id"),
                    )

                    name = member.display_name if member else "Unknown"

                    lines.append(
                        f"- {name} | {status} | {'ONLINE' if online else 'OFFLINE'}"
                    )


        chunks = self._chunk("\n".join(lines))

        embed = info_embed(
            "Staff VRChat Status",
            "Shows who the bot thinks is online.",
        )

        await respond(ctx, embed=embed, ephemeral=True)

        for c in chunks:

            await ctx.send(f"```\n{c}\n```")


    # ============================================================
    # ARCHIVED STAFF RECORD
    # ============================================================

    def _get_archive(self):

        return leaderboard_data.get("archive", {})


    @commands.hybrid_command(
        name="staffrecordarchived",
        description="View archived staff record",
    )
    async def staffrecordarchived(self, ctx, staff: str):

        archive = self._get_archive()

        record = archive.get(staff)

        if not record:

            await respond(
                ctx,
                embed=warning_embed(
                    "Not Found",
                    "No archived record found",
                ),
                ephemeral=True,
            )

            return


        embed = info_embed(f"Archived Record — {staff}")

        embed.add_field(
            name="Warn",
            value=str(record.get("warn", 0)),
        )

        embed.add_field(
            name="Kick",
            value=str(record.get("kick", 0)),
        )

        embed.add_field(
            name="Ban",
            value=str(record.get("ban", 0)),
        )

        embed.add_field(
            name="Points",
            value=str(record.get("points", 0)),
        )

        embed.add_field(
            name="Archived",
            value=str(record.get("archived_at")),
        )

        await respond(ctx, embed=embed, ephemeral=True)

async def setup(bot):

    await bot.add_cog(ConsigliereCommands(bot))
