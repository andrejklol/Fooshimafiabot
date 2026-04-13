import discord
from discord.ext import commands

from core.cache import app_state
from core.config import GUILD_ID, STAFF_ALERT_ORDER
from core.embeds import info_embed, success_embed, warning_embed
from core.utils import respond

from services.high_staff import track_high_staff_action
from services.leaderboard.processors import sync_all_vrc_staff_into_leaderboard
from services.leaderboard.storage import leaderboard_data
from services.vrchat_client import (
    get_all_vrc_staff_members,
    get_vrchat_user_status,
    refresh_vrc_group_members,
)

from .permissions import check_level, LEVEL_CONSIGLIERE


class ConsigliereCommands(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ============================================================
    # HELPERS
    # ============================================================

    def _chunk(self, text: str, limit: int = 1900) -> list[str]:
        if len(text) <= limit:
            return [text]

        parts: list[str] = []
        current = ""

        for line in text.splitlines():
            line_with_newline = f"{line}\n"

            if len(current) + len(line_with_newline) > limit:
                if current:
                    parts.append(current.rstrip())

                current = line_with_newline
            else:
                current += line_with_newline

        if current:
            parts.append(current.rstrip())

        return parts


    def _get_archive(self) -> dict:
        data = leaderboard_data.get("archive", {})
        return data if isinstance(data, dict) else {}


    async def _run_refresh_vrc_members(self):

        await refresh_vrc_group_members(force=True)

        vrc_staff_members = await get_all_vrc_staff_members(force_refresh=False)

        synced_count = await sync_all_vrc_staff_into_leaderboard(self.bot)

        return (
            len(app_state.vrc_group_member_roles),
            len(app_state.vrc_group_role_map),
            len(vrc_staff_members),
            synced_count,
        )


    # ============================================================
    # REFRESH VRC MEMBERS
    # ============================================================

    @commands.hybrid_command(
        name="refreshvrcmembers",
        description="Refresh cached VRChat group members and roles",
    )
    async def refreshvrcmembers(self, ctx: commands.Context):

        if not await check_level(ctx, LEVEL_CONSIGLIERE):

            await respond(
                ctx,
                embed=warning_embed(
                    "Permission Denied",
                    "You do not have permission to use this command.",
                ),
                ephemeral=True,
            )

            return

        try:

            if getattr(ctx, "interaction", None) and not ctx.interaction.response.is_done():

                await ctx.interaction.response.defer(ephemeral=True)


            member_count, role_count, staff_count, synced_count = (
                await self._run_refresh_vrc_members()
            )


            description = "\n".join(
                [
                    f"Members cached: `{member_count}`",
                    f"Roles cached: `{role_count}`",
                    f"Detected VRC staff members: `{staff_count}`",
                    f"Staff synced to leaderboard: `{synced_count}`",
                ]
            )


            await respond(
                ctx,
                embed=success_embed(
                    "VRChat Members Refreshed",
                    description,
                ),
                ephemeral=True,
            )


        except Exception as exc:

            await respond(
                ctx,
                embed=warning_embed(
                    "Refresh Failed",
                    str(exc),
                ),
                ephemeral=True,
            )


    # ============================================================
    # STAFF STATUS
    # ============================================================

    @commands.hybrid_command(
        name="staffstatus",
        description="Show VRChat online status of staff",
    )
    async def staffstatus(self, ctx: commands.Context):

        if not await check_level(ctx, LEVEL_CONSIGLIERE):

            await respond(
                ctx,
                embed=warning_embed(
                    "Permission Denied",
                    "You do not have permission to use this command.",
                ),
                ephemeral=True,
            )

            return


        guild = self.bot.get_guild(GUILD_ID)

        if not guild:

            await respond(
                ctx,
                embed=warning_embed(
                    "Guild Missing",
                    "Bot could not find guild.",
                ),
                ephemeral=True,
            )

            return


        try:

            if getattr(ctx, "interaction", None) and not ctx.interaction.response.is_done():

                await ctx.interaction.response.defer(ephemeral=True)


            lines: list[str] = []


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


            await respond(
                ctx,
                embed=info_embed(
                    "Staff VRChat Status",
                    "Shows who the bot thinks is online.",
                ),
                ephemeral=True,
            )


            for chunk in chunks:

                await ctx.send(
                    f"```\n{chunk}\n```",
                    delete_after=120,
                )


        except Exception as exc:

            await respond(
                ctx,
                embed=warning_embed(
                    "Staff Status Failed",
                    str(exc),
                ),
                ephemeral=True,
            )


    # ============================================================
    # ARCHIVED STAFF RECORD
    # ============================================================

    @commands.hybrid_command(
        name="staffrecordarchived",
        description="View archived staff record",
    )
    async def staffrecordarchived(self, ctx: commands.Context, staff: str):

        if not await check_level(ctx, LEVEL_CONSIGLIERE):

            await respond(
                ctx,
                embed=warning_embed(
                    "Permission Denied",
                    "You do not have permission to use this command.",
                ),
                ephemeral=True,
            )

            return


        archive = self._get_archive()

        record = archive.get(staff)


        if not record:

            await respond(
                ctx,
                embed=warning_embed(
                    "Not Found",
                    "No archived record found.",
                ),
                ephemeral=True,
            )

            return


        embed = info_embed(
            f"Archived Record — {staff}",
        )


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


        await respond(
            ctx,
            embed=embed,
            ephemeral=True,
        )


    # ============================================================
    # TEST HIGH STAFF ALERT
    # ============================================================

    @commands.hybrid_command(
        name="testhighstaff",
        description="Test high staff / suspicious mod alerts",
    )
    async def testhighstaff(self, ctx: commands.Context, action: str):

        if not await check_level(ctx, LEVEL_CONSIGLIERE):

            await respond(
                ctx,
                embed=warning_embed(
                    "Permission Denied",
                    "You do not have permission to use this command.",
                ),
                ephemeral=True,
            )

            return


        action = str(action or "").lower().strip()


        if action not in {"warn", "kick", "ban"}:

            await respond(
                ctx,
                embed=warning_embed(
                    "Invalid Action",
                    "Use `warn`, `kick`, or `ban`.",
                ),
                ephemeral=True,
            )

            return


        try:

            if getattr(ctx, "interaction", None) and not ctx.interaction.response.is_done():

                await ctx.interaction.response.defer(ephemeral=True)


            for i in range(12):

                await track_high_staff_action(

                    moderator_name=ctx.author.display_name,

                    action_type=action,

                    vrchat_user_id=str(ctx.author.id),

                    target_id=f"test_user_{i}",

                    target_name=f"TestUser{i}",

                )


            await respond(
                ctx,
                embed=success_embed(
                    "Test Triggered",
                    f"Simulated **{action}** spike.\nCheck alert channel.",
                ),
                ephemeral=True,
            )


        except Exception as exc:

            await respond(
                ctx,
                embed=warning_embed(
                    "Test Failed",
                    str(exc),
                ),
                ephemeral=True,
            )


async def setup(bot: commands.Bot):

    await bot.add_cog(
        ConsigliereCommands(bot)
    )
