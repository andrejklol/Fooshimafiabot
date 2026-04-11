from typing import Optional
import re
from difflib import SequenceMatcher

import discord
from discord import app_commands
from discord.ext import commands

from core.cache import app_state
from core.config import (
    ERROR_LOG_CHANNEL_ID,
    GUILD_ID,
    MAX_HISTORY_LOAD,
    REPEAT_BAN_THRESHOLD,
    REPEAT_BAN_WINDOW_DAYS,
    REPEAT_KICK_THRESHOLD,
    REPEAT_KICK_WINDOW_DAYS,
    REPEAT_WARN_THRESHOLD,
    REPEAT_WARN_WINDOW_DAYS,
    STAFF_ALERT_ORDER,
)
from core.embeds import (
    info_embed,
    leaderboard_embed,
    owner_embed,
    success_embed,
    warning_embed,
)
from core.error_embed import send_error_embed
from core.utils import (
    format_remaining_cooldown,
    respond,
    send_error_log,
    vrchat_cooldown_active,
)
from services.alerts import send_repeat_alert
from services.leaderboard.processors import sync_all_vrc_staff_into_leaderboard
from services.leaderboard.queries import get_top_staff
from services.leaderboard.scoring import build_score_footer
from services.leaderboard.service import (
    load_full_history,
    reset_leaderboard_data,
    reset_monthly_leaderboard_data,
)
from services.leaderboard.storage import leaderboard_data
from services.offenders.storage import reset_repeat_offenders
from services.vrchat_client import (
    get_all_vrc_staff_members,
    get_vrchat_user_status,
    refresh_vrc_group_members,
)

# ============================================================
# OWNER OVERRIDE + MAFIA ROLE PERMISSIONS
# ============================================================

OWNER_USER_ID = 1470116079459242139

ROLE_IDS = {
    "godfooshi": 1470116079459242139,
    "underboss": 1470116287010046166,
    "consigliere": 1487940866570977301,
    "capo": 1470116372007616683,
    "soldier": 1479536640316805296,
    "associate": 1470116440982949898,
}

ROLE_LEVELS = {
    ROLE_IDS["godfooshi"]: 5,
    ROLE_IDS["underboss"]: 4,
    ROLE_IDS["consigliere"]: 3,
    ROLE_IDS["capo"]: 2,
    ROLE_IDS["soldier"]: 1,
    ROLE_IDS["associate"]: 0,
}

LEVEL_OWNER = 5
LEVEL_UNDERBOSS = 4
LEVEL_CONSIGLIERE = 3
LEVEL_CAPO = 2
LEVEL_SOLDIER = 1
LEVEL_ASSOCIATE = 0

_PERMISSION_DENIED_EMBED = warning_embed(
    "Permission Denied",
    "You do not have permission to use this command.",
)

# ============================================================
# LEADERBOARD / MODERATION CONSTANTS
# ============================================================

_VALID_SCOPES = {"overall", "monthly"}
_AUTOCOMPLETE_SCOPES = ["overall", "monthly"]

_SEVERITY_THRESHOLDS = {
    "warn": (REPEAT_WARN_THRESHOLD, REPEAT_WARN_WINDOW_DAYS),
    "kick": (REPEAT_KICK_THRESHOLD, REPEAT_KICK_WINDOW_DAYS),
    "ban": (REPEAT_BAN_THRESHOLD, REPEAT_BAN_WINDOW_DAYS),
}

_SIMULATE_TRIGGERED: dict[str, list[tuple]] = {
    "warn": [("warn", 3, 7, 3)],
    "kick": [("warn", 5, 7, 3), ("kick", 2, 30, 2)],
    "ban": [("warn", 6, 7, 3), ("kick", 3, 30, 2), ("ban", 1, 30, 1)],
}


# ============================================================
# SYNC HELPER
# ============================================================

async def perform_command_sync(bot: commands.Bot, clear_guild: bool = False) -> str:
    if not GUILD_ID:
        synced = await bot.tree.sync()
        return f"Globally synced {len(synced)} commands."

    guild = discord.Object(id=GUILD_ID)

    if clear_guild:
        bot.tree.clear_commands(guild=guild)
        await bot.tree.sync(guild=guild)

    bot.tree.copy_global_to(guild=guild)
    synced = await bot.tree.sync(guild=guild)
    return f"Guild synced {len(synced)} commands."


class Commands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ============================================================
    # PERMISSION HELPERS
    # ============================================================

    def _get_permission_level(self, member: discord.Member | discord.User) -> int:
        if member.id == OWNER_USER_ID:
            return 999

        roles = getattr(member, "roles", None)
        if not roles:
            return 0

        highest = 0
        for role in roles:
            highest = max(highest, ROLE_LEVELS.get(role.id, 0))

        return highest

    async def _require_level(self, ctx, required_level: int) -> bool:
        level = self._get_permission_level(ctx.author)

        print(
            f"[commands] permission check "
            f"author_id={ctx.author.id} "
            f"level={level} required={required_level}"
        )

        if level >= required_level:
            print("[commands] permission granted")
            return True

        print("[commands] permission denied")
        await respond(ctx, embed=_PERMISSION_DENIED_EMBED, ephemeral=True)
        return False

    # ============================================================
    # RESPONSE / ERROR HELPERS
    # ============================================================

    async def _defer_if_interaction(self, ctx, ephemeral: bool = True) -> None:
        if getattr(ctx, "interaction", None):
            if not ctx.interaction.response.is_done():
                print("[commands] deferring interaction response")
                await ctx.interaction.response.defer(ephemeral=ephemeral)

    async def _send_ctx_reply(
            self,
            ctx,
            content: str | None = None,
            embed: discord.Embed | None = None,
            ephemeral: bool = True,
    ) -> None:
        if getattr(ctx, "interaction", None):
            print("[commands] sending interaction followup")
            await ctx.interaction.followup.send(
                content=content,
                embed=embed,
                ephemeral=ephemeral,
            )
        else:
            print("[commands] sending normal ctx reply")
            await ctx.send(content=content, embed=embed)

    async def _handle_admin_error(
            self,
            ctx,
            user_title: str,
            log_title: str,
            exc: Exception,
    ) -> None:
        print(f"[commands] error in {log_title}: {exc!r}")

        await self._send_ctx_reply(
            ctx,
            embed=warning_embed(user_title, str(exc)),
            ephemeral=True,
        )

        await send_error_log(log_title, exc)

        await send_error_embed(
            self.bot,
            ERROR_LOG_CHANNEL_ID,
            title=log_title,
            description=str(exc),
            trace_id="commands_admin_error",
            extra={"exception": repr(exc)},
            level="error",
        )

    async def _handle_mod_error(
            self,
            ctx,
            user_title: str,
            log_title: str,
            exc: Exception,
    ) -> None:
        await respond(
            ctx,
            embed=warning_embed(user_title, str(exc)),
            ephemeral=True,
        )
        await send_error_log(log_title, exc)

    # ============================================================
    # TEXT / STATUS HELPERS
    # ============================================================

    def _chunk_text(self, text: str, limit: int = 1900) -> list[str]:
        if len(text) <= limit:
            return [text]

        chunks: list[str] = []
        current = ""

        for line in text.splitlines(keepends=True):
            if len(current) + len(line) > limit:
                if current:
                    chunks.append(current)
                current = line
            else:
                current += line

        if current:
            chunks.append(current)

        return chunks

    def _format_staff_debug_block(
            self,
            discord_id: int,
            display_name: str,
            discord_status: str,
            vrc_input: str,
            resolved_user_id: str | None,
            raw_status: str | None,
            is_online: bool,
    ) -> str:
        return "\n".join(
            [
                f"- Discord: <@{discord_id}>",
                f"  Name: {display_name}",
                f"  Discord Status: {discord_status}",
                f"  VRC Input: `{vrc_input}`",
                f"  VRC Resolved: `{resolved_user_id or 'not resolved'}`",
                f"  VRC Status: {raw_status or 'unknown'}",
                f"  Bot Thinks: **{'ONLINE' if is_online else 'OFFLINE'}**",
            ]
        )

    def _normalize_history_summary(
            self,
            result,
            *,
            requested: int,
            rebuild: bool,
            monthly_only: bool,
    ) -> dict[str, int | float | bool]:
        if isinstance(result, dict):
            invites_value = int(
                result.get("invite_accepts", result.get("invites", 0)) or 0
            )

            return {
                "fetched": int(result.get("fetched", 0) or 0),
                "counted_total": int(result.get("counted_total", 0) or 0),
                "warns": int(result.get("warns", 0) or 0),
                "kicks": int(result.get("kicks", 0) or 0),
                "bans": int(result.get("bans", 0) or 0),
                "invites": invites_value,
                "skipped": int(result.get("skipped", 0) or 0),
                "elapsed_seconds": float(result.get("elapsed_seconds", 0.0) or 0.0),
                "requested": requested,
                "rebuild": rebuild,
                "monthly_only": monthly_only,
            }

        loaded = int(result or 0)
        return {
            "fetched": loaded,
            "counted_total": loaded,
            "warns": 0,
            "kicks": 0,
            "bans": 0,
            "invites": 0,
            "skipped": max(requested - loaded, 0),
            "elapsed_seconds": 0.0,
            "requested": requested,
            "rebuild": rebuild,
            "monthly_only": monthly_only,
        }

    def _build_history_summary_text(self, summary: dict[str, int | float | bool]) -> str:
        elapsed = float(summary["elapsed_seconds"])
        elapsed_text = f"{elapsed:.2f}s" if elapsed > 0 else "N/A"

        return (
            f"Fetched: `{summary['fetched']}`\n"
            f"Counted Total: `{summary['counted_total']}`\n"
            f"Warnings: `{summary['warns']}`\n"
            f"Kicks: `{summary['kicks']}`\n"
            f"Bans: `{summary['bans']}`\n"
            f"Invites: `{summary['invites']}`\n"
            f"Skipped: `{summary['skipped']}`\n"
            f"Elapsed: `{elapsed_text}`\n"
            f"Requested: `{summary['requested']}`\n"
            f"Rebuild mode: `{summary['rebuild']}`\n"
            f"Monthly only: `{summary['monthly_only']}`"
        )

    # ============================================================
    # LEADERBOARD DATA HELPERS
    # ============================================================

    def _get_scope_data(self, scope: str = "staff") -> dict:
        data = leaderboard_data.get(scope, {})
        return data if isinstance(data, dict) else {}

    def _normalize_staff_search_text(self, text: str) -> str:
        return re.sub(r"[^a-z0-9]", "", str(text or "").lower())

    def _name_similarity(self, a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio()

    def _match_record_against_names(
            self,
            record: dict,
            candidates: list[str],
    ) -> bool:
        record_name = str(record.get("name", "")).strip().lower()

        if not record_name:
            return False

        record_norm = self._normalize_staff_search_text(record_name)

        if not record_norm:
            return False

        for candidate in candidates:
            candidate = str(candidate or "").strip().lower()
            if not candidate:
                continue

            candidate_norm = self._normalize_staff_search_text(candidate)
            if not candidate_norm:
                continue

            if record_norm == candidate_norm:
                return True

            if candidate_norm in record_norm or record_norm in candidate_norm:
                return True

            if self._name_similarity(record_norm, candidate_norm) >= 0.88:
                return True

        return False

    def _find_staff_record_by_member(
            self,
            member: discord.Member | discord.User,
    ) -> tuple[str | None, dict | None]:
        scope_data = self._get_scope_data("staff")
        if not scope_data:
            return None, None

        member_id_str = str(member.id)
        candidates = [
            getattr(member, "display_name", ""),
            getattr(member, "name", ""),
            getattr(member, "global_name", ""),
        ]

        for staff_id, record in scope_data.items():
            record_discord_id = record.get("discord_id")
            if record_discord_id is not None and str(record_discord_id) == member_id_str:
                return staff_id, record

        for staff_id, record in scope_data.items():
            if self._match_record_against_names(record, candidates):
                return staff_id, record

        return None, None

    def _find_staff_record(
            self,
            query: str,
    ) -> tuple[str | None, dict | None]:
        scope_data = self._get_scope_data("staff")
        if not scope_data:
            return None, None

        raw_query = str(query or "").strip()
        lowered = raw_query.lower()
        normalized_query = self._normalize_staff_search_text(raw_query)

        if not raw_query:
            return None, None

        if raw_query in scope_data:
            return raw_query, scope_data[raw_query]

        if lowered in scope_data:
            return lowered, scope_data[lowered]

        for staff_id, record in scope_data.items():
            record_discord_id = record.get("discord_id")
            if record_discord_id is not None and str(record_discord_id).lower() == lowered:
                return staff_id, record

        for staff_id, record in scope_data.items():
            name = str(record.get("name", "")).strip()
            if not name:
                continue

            if name.lower() == lowered:
                return staff_id, record

        for staff_id, record in scope_data.items():
            name = str(record.get("name", "")).strip()
            name_norm = self._normalize_staff_search_text(name)

            if not name_norm or not normalized_query:
                continue

            if name_norm == normalized_query:
                return staff_id, record

            if normalized_query in name_norm or name_norm in normalized_query:
                return staff_id, record

        best_match: tuple[str | None, dict | None, float] = (None, None, 0.0)

        for staff_id, record in scope_data.items():
            name = str(record.get("name", "")).strip()
            name_norm = self._normalize_staff_search_text(name)

            if not name_norm or not normalized_query:
                continue

            ratio = self._name_similarity(name_norm, normalized_query)

            if ratio > best_match[2]:
                best_match = (staff_id, record, ratio)

        if best_match[0] is not None and best_match[2] >= 0.88:
            return best_match[0], best_match[1]

        return None, None

    def _get_staff_rank(self, staff_id: str, scope: str = "staff") -> Optional[int]:
        ranked = get_top_staff(limit=9999, scope=scope)

        for index, staff in enumerate(ranked, start=1):
            current_id = str(staff.get("id") or "").strip()
            if current_id == staff_id:
                return index

        return None

    def _get_top_stat_lines(
            self,
            stat_key: str,
            scope: str,
            limit: int = 3,
    ) -> list[str]:
        scope_data = self._get_scope_data(scope)

        ranked = sorted(
            scope_data.values(),
            key=lambda x: int(x.get(stat_key, 0) or 0),
            reverse=True,
        )

        ranked = [
            staff
            for staff in ranked
            if int(staff.get(stat_key, 0) or 0) > 0
        ][:limit]

        medal_map = {
            1: "🥇",
            2: "🥈",
            3: "🥉",
        }

        lines: list[str] = []

        for index, staff in enumerate(ranked, start=1):
            name = str(staff.get("name", "Unknown"))
            value = int(staff.get(stat_key, 0) or 0)
            medal = medal_map.get(index, f"`#{index}`")
            lines.append(f"{medal} {name} — `{value}`")

        return lines

    def _build_staffrecord_embed(
            self,
            staff_id: str,
            record: dict,
            member: Optional[discord.Member | discord.User] = None,
    ) -> discord.Embed:
        name = record.get("name") or staff_id
        warn_count = int(record.get("warn", 0) or 0)
        kick_count = int(record.get("kick", 0) or 0)
        ban_count = int(record.get("ban", 0) or 0)
        invite_count = int(record.get("invite_accept", 0) or 0)
        points = int(record.get("points", 0) or 0)

        rank = self._get_staff_rank(staff_id, scope="staff")

        embed = info_embed(f"Staff Record — {name}")

        embed.add_field(name="Name", value=str(name), inline=True)
        embed.add_field(name="Staff ID", value=f"`{staff_id}`", inline=True)
        embed.add_field(name="Points", value=f"`{points}`", inline=True)

        if member is not None:
            embed.add_field(name="Discord", value=member.mention, inline=True)
        else:
            record_discord_id = record.get("discord_id")
            if record_discord_id:
                embed.add_field(name="Discord", value=f"<@{record_discord_id}>", inline=True)

        embed.add_field(name="Warnings", value=f"`{warn_count}`", inline=True)
        embed.add_field(name="Kicks", value=f"`{kick_count}`", inline=True)
        embed.add_field(name="Bans", value=f"`{ban_count}`", inline=True)
        embed.add_field(name="Invites", value=f"`{invite_count}`", inline=True)

        total_actions = warn_count + kick_count + ban_count + invite_count
        embed.add_field(name="Tracked Actions", value=f"`{total_actions}`", inline=True)

        embed.add_field(
            name="Overall Rank",
            value=f"`#{rank}`" if rank is not None else "`Unranked`",
            inline=True,
        )

        embed.set_footer(text=build_score_footer())
        return embed

    # ============================================================
    # ADMIN COMMANDS
    # ============================================================

    @commands.hybrid_command(
        name="refreshvrcmembers",
        description="Refresh cached VRChat group members and roles",
    )
    async def refreshvrcmembers(self, ctx: commands.Context) -> None:
        if not await self._require_level(ctx, LEVEL_CONSIGLIERE):
            return

        await self._defer_if_interaction(ctx)

        try:
            await refresh_vrc_group_members(force=True)
            vrc_staff_members = await get_all_vrc_staff_members(force_refresh=False)
            synced_count = await sync_all_vrc_staff_into_leaderboard(vrc_staff_members)

            await self._send_ctx_reply(
                ctx,
                embed=success_embed(
                    "VRChat Members Refreshed",
                    f"Members cached: `{len(app_state.vrc_group_member_roles)}`\n"
                    f"Roles cached: `{len(app_state.vrc_group_role_map)}`\n"
                    f"Staff synced to leaderboard: `{synced_count}`",
                ),
                ephemeral=True,
            )

        except Exception as exc:
            await self._handle_admin_error(
                ctx,
                "Refresh Failed",
                "Refresh Members Error",
                exc,
            )

    @commands.hybrid_command(
        name="resetvrcdata",
        description="Reset leaderboard and repeat offender data",
    )
    async def resetvrcdata(self, ctx: commands.Context) -> None:
        if not await self._require_level(ctx, LEVEL_OWNER):
            return

        try:
            reset_leaderboard_data()
            reset_repeat_offenders()

            await respond(
                ctx,
                embed=owner_embed(
                    "VRChat Data Reset",
                    "Leaderboard and repeat offender data have been reset.",
                ),
                ephemeral=True,
            )

        except Exception as exc:
            await self._handle_admin_error(
                ctx,
                "Reset Failed",
                "Reset Leaderboard Error",
                exc,
            )

    @commands.hybrid_command(
        name="loadvrchistory",
        description="Load past VRChat audit logs",
    )
    @app_commands.describe(
        amount="How many past logs to scan",
        rebuild="Reset and rebuild leaderboard from fetched history",
        monthly_only="Rebuild only this month's leaderboard counters",
    )
    async def loadvrchistory(
            self,
            ctx: commands.Context,
            amount: int = 5000,
            rebuild: bool = True,
            monthly_only: bool = False,
    ) -> None:
        print(
            f"[loadvrchistory] called by user={ctx.author.id} "
            f"amount={amount} rebuild={rebuild} monthly_only={monthly_only}"
        )

        if not await self._require_level(ctx, LEVEL_OWNER):
            print("[loadvrchistory] permission denied")
            return

        if amount <= 0:
            print("[loadvrchistory] invalid amount")
            await respond(
                ctx,
                embed=warning_embed(
                    "Invalid Amount",
                    "Amount must be greater than 0.",
                ),
                ephemeral=True,
            )
            return

        amount = min(amount, MAX_HISTORY_LOAD)
        print(f"[loadvrchistory] clamped amount={amount}")

        if vrchat_cooldown_active():
            print("[loadvrchistory] cooldown active")
            await respond(
                ctx,
                embed=warning_embed(
                    "VRChat Cooldown Active",
                    f"Try again later.\n`{format_remaining_cooldown()}`",
                ),
                ephemeral=True,
            )
            return

        await self._defer_if_interaction(ctx)

        try:
            result = await load_full_history(
                limit=amount,
                rebuild=rebuild,
                monthly_only=monthly_only,
            )

            summary = self._normalize_history_summary(
                result,
                requested=amount,
                rebuild=rebuild,
                monthly_only=monthly_only,
            )

            await self._send_ctx_reply(
                ctx,
                embed=success_embed(
                    "History Load Complete",
                    self._build_history_summary_text(summary),
                ),
                ephemeral=True,
            )

        except Exception as exc:
            await send_error_embed(
                self.bot,
                ERROR_LOG_CHANNEL_ID,
                title="Leaderboard Failure",
                description="loadvrchistory raised an exception",
                trace_id="leaderboard",
                extra={
                    "error": repr(exc),
                    "amount": amount,
                    "rebuild": rebuild,
                    "monthly_only": monthly_only,
                },
                level="error",
            )

            await self._handle_admin_error(
                ctx,
                "Load History Failed",
                "Load History Error",
                exc,
            )

    @commands.hybrid_command(
        name="synccommands",
        description="Sync slash commands",
    )
    @app_commands.describe(clear_guild="Clear current guild commands before syncing")
    async def synccommands(self, ctx: commands.Context, clear_guild: bool = False) -> None:
        if not await self._require_level(ctx, LEVEL_OWNER):
            return

        await self._defer_if_interaction(ctx)

        try:
            result = await perform_command_sync(self.bot, clear_guild=clear_guild)

            await self._send_ctx_reply(
                ctx,
                embed=success_embed("Commands Synced", result),
                ephemeral=True,
            )

        except Exception as exc:
            await self._handle_admin_error(
                ctx,
                "Sync Failed",
                "Sync Command Error",
                exc,
            )

    @commands.hybrid_command(
        name="staffstatus",
        description="Show which staff the bot thinks are online in VRChat",
    )
    async def staffstatus(self, ctx: commands.Context) -> None:
        if not await self._require_level(ctx, LEVEL_CONSIGLIERE):
            return

        guild = self.bot.get_guild(GUILD_ID)
        if guild is None:
            await respond(
                ctx,
                embed=warning_embed("Guild Not Found", "Guild not found."),
                ephemeral=True,
            )
            return

        await self._defer_if_interaction(ctx)

        try:
            lines: list[str] = []
            seen_users: set[tuple] = set()

            for action_type, rank_groups in STAFF_ALERT_ORDER.items():
                lines.append(f"## {action_type.upper()}")

                for rank_name, entries in rank_groups:
                    lines.append(f"**{rank_name}**")

                    for entry in entries:
                        discord_id = entry.get("discord_id")
                        vrchat_user_id = entry.get("vrchat_user_id")
                        vrchat_username = entry.get("vrchat_username")
                        unique_key = (discord_id, vrchat_user_id, vrchat_username)

                        if unique_key in seen_users:
                            continue

                        seen_users.add(unique_key)

                        member = guild.get_member(discord_id) if discord_id else None
                        display_name = (
                            f"{member.display_name} ({member.name})"
                            if member
                            else f"Unknown Member ({discord_id})"
                        )
                        discord_status = str(member.status) if member else "missing"

                        is_online, resolved_user_id, raw_status = await get_vrchat_user_status(
                            vrchat_username=vrchat_username,
                            vrchat_user_id=vrchat_user_id,
                        )

                        lines.append(
                            self._format_staff_debug_block(
                                discord_id=discord_id,
                                display_name=display_name,
                                discord_status=discord_status,
                                vrc_input=vrchat_user_id or vrchat_username or "missing",
                                resolved_user_id=resolved_user_id,
                                raw_status=raw_status,
                                is_online=is_online,
                            )
                        )

                    lines.append("")

            chunks = self._chunk_text("\n".join(lines).strip())
            header = info_embed(
                "Staff VRChat Status",
                "Shows which staff the bot currently thinks are online in VRChat.",
            )

            if getattr(ctx, "interaction", None):
                await ctx.interaction.followup.send(embed=header, ephemeral=True)

                for chunk in chunks:
                    await ctx.interaction.followup.send(
                        f"```md\n{chunk}\n```",
                        ephemeral=True,
                    )
            else:
                await ctx.reply(embed=header, mention_author=False)

                for chunk in chunks:
                    await ctx.send(f"```md\n{chunk}\n```")

        except Exception as exc:
            await self._handle_admin_error(
                ctx,
                "Staff Status Check Failed",
                "Debug VRC Staff Error",
                exc,
            )

    @commands.hybrid_command(
        name="simulaterepeatalert",
        description="Simulate a repeat offender alert",
    )
    @app_commands.describe(action="Type of alert to simulate")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="warn", value="warn"),
            app_commands.Choice(name="kick", value="kick"),
            app_commands.Choice(name="ban", value="ban"),
        ]
    )
    async def simulaterepeatalert(
            self,
            ctx: commands.Context,
            action: app_commands.Choice[str],
    ) -> None:
        if not await self._require_level(ctx, LEVEL_OWNER):
            return

        await self._defer_if_interaction(ctx)

        try:
            await send_repeat_alert(
                pretty_name="TestUser",
                target_id="usr_test_repeat_user",
                triggered=_SIMULATE_TRIGGERED[action.value],
                highest_action=action.value,
            )

            await self._send_ctx_reply(
                ctx,
                embed=success_embed(
                    "Repeat Alert Simulated",
                    f"Simulated {action.value.upper()} repeat offender alert.",
                ),
                ephemeral=True,
            )

        except Exception as exc:
            await self._handle_admin_error(
                ctx,
                "Simulation Failed",
                "Test Repeat Alert Error",
                exc,
            )

    @commands.hybrid_command(
        name="testerror",
        description="Send a test error to the error log channel",
    )
    async def testerror(self, ctx: commands.Context) -> None:
        if not await self._require_level(ctx, LEVEL_OWNER):
            return

        await self._defer_if_interaction(ctx)

        try:
            _ = 1 / 0

        except Exception as exc:
            await send_error_embed(
                self.bot,
                ERROR_LOG_CHANNEL_ID,
                title="Test Error Triggered",
                description="Manual test error triggered by command",
                trace_id="testerror",
                extra={
                    "error": repr(exc),
                    "user": f"{ctx.author} ({ctx.author.id})",
                    "guild": getattr(ctx.guild, "name", "DM"),
                    "channel_id": getattr(ctx.channel, "id", "unknown"),
                },
                level="error",
            )

            await self._send_ctx_reply(
                ctx,
                embed=success_embed(
                    "Test Error Sent",
                    "A test error was successfully sent to the error log channel.",
                ),
                ephemeral=True,
            )

    # ============================================================
    # MODERATION / LEADERBOARD COMMANDS
    # ============================================================

    @commands.hybrid_command(
        name="clear",
        description="Delete recent messages in this channel",
    )
    @app_commands.describe(amount="Number of messages to delete (max 100)")
    async def clear(self, ctx: commands.Context, amount: int) -> None:
        if not await self._require_level(ctx, LEVEL_CAPO):
            return

        if amount <= 0:
            await respond(
                ctx,
                embed=warning_embed(
                    "Invalid Amount",
                    "Amount must be greater than 0.",
                ),
                ephemeral=True,
            )
            return

        if ctx.channel is None:
            await respond(
                ctx,
                embed=warning_embed(
                    "Clear Failed",
                    "This command can only be used in a server channel.",
                ),
                ephemeral=True,
            )
            return

        amount = min(amount, 100)

        await self._defer_if_interaction(ctx)

        try:
            deleted = await ctx.channel.purge(limit=amount)

            await self._send_ctx_reply(
                ctx,
                embed=success_embed(
                    "Messages Cleared",
                    f"🧹 Deleted `{len(deleted)}` messages.",
                ),
                ephemeral=True,
            )

        except Exception as exc:
            await self._handle_mod_error(
                ctx,
                "Clear Failed",
                "Clear Command Error",
                exc,
            )

    @commands.hybrid_command(
        name="leaderboard",
        description="Show VRChat moderation leaderboard ranked by points",
    )
    @app_commands.describe(
        scope="Choose overall or monthly",
    )
    async def leaderboard(
            self,
            ctx,
            scope: str = "overall",
    ) -> None:
        if not await self._require_level(ctx, LEVEL_SOLDIER):
            return

        try:
            scope = str(scope or "overall").lower().strip()

            if scope not in _VALID_SCOPES:
                await respond(
                    ctx,
                    embed=warning_embed(
                        "Invalid Scope",
                        "Use `overall` or `monthly`.",
                    ),
                    ephemeral=True,
                )
                return

            data_scope = "monthly" if scope == "monthly" else "staff"

            top_points = self._get_top_stat_lines("points", data_scope, 3)
            top_warns = self._get_top_stat_lines("warn", data_scope, 3)
            top_kicks = self._get_top_stat_lines("kick", data_scope, 3)
            top_bans = self._get_top_stat_lines("ban", data_scope, 3)
            top_invites = self._get_top_stat_lines("invite", data_scope, 3)

            description_parts = [
                "**Top Points**",
                "\n".join(top_points) if top_points else "No data yet.",
                "",
                "**Top Warnings**",
                "\n".join(top_warns) if top_warns else "No data yet.",
                "",
                "**Top Kicks**",
                "\n".join(top_kicks) if top_kicks else "No data yet.",
                "",
                "**Top Bans**",
                "\n".join(top_bans) if top_bans else "No data yet.",
                "",
                "**Top Invites**",
                "\n".join(top_invites) if top_invites else "No data yet.",
            ]

            embed = leaderboard_embed(
                f"VRChat Moderation Leaderboard — {scope.title()}",
                "\n".join(description_parts),
            )

            embed.set_footer(text=build_score_footer())
            await respond(ctx, embed=embed)

        except Exception as exc:
            await self._handle_mod_error(
                ctx,
                "Leaderboard Failed",
                "Leaderboard Command Error",
                exc,
            )

    @commands.hybrid_command(
        name="staffrecord",
        description="Look up an individual staff member's moderation record",
    )
    @app_commands.describe(
        member="Mention the Discord staff member",
        staff="Or type the leaderboard name / VRChat ID",
    )
    async def staffrecord(
            self,
            ctx: commands.Context,
            member: Optional[discord.Member] = None,
            *,
            staff: Optional[str] = None,
    ) -> None:
        if not await self._require_level(ctx, LEVEL_SOLDIER):
            return

        try:
            staff_id = None
            record = None
            resolved_member: Optional[discord.Member] = member

            if member is not None:
                staff_id, record = self._find_staff_record_by_member(member)

            if (not staff_id or not record) and staff:
                staff_id, record = self._find_staff_record(staff)

                if record and resolved_member is None and ctx.guild is not None:
                    record_discord_id = record.get("discord_id")
                    if record_discord_id:
                        resolved_member = ctx.guild.get_member(int(record_discord_id))

            if not staff_id or not record:
                await respond(
                    ctx,
                    embed=warning_embed(
                        "Staff Record Not Found",
                        "No staff record matched that Discord user, name, or ID.",
                    ),
                    ephemeral=True,
                )
                return

            embed = self._build_staffrecord_embed(staff_id, record, resolved_member)
            await respond(ctx, embed=embed, ephemeral=True)

        except Exception as exc:
            await self._handle_mod_error(
                ctx,
                "Staff Record Failed",
                "Staff Record Command Error",
                exc,
            )

    @staffrecord.autocomplete("staff")
    async def staffrecord_autocomplete(
            self,
            interaction: discord.Interaction,
            current: str,
    ) -> list[app_commands.Choice[str]]:
        try:
            current = str(current or "").lower().strip()
            scope_data = self._get_scope_data("staff")

            choices: list[app_commands.Choice[str]] = []

            for staff_id, record in scope_data.items():
                name = str(record.get("name", staff_id))
                haystack = f"{name} {staff_id}".lower()

                if current in haystack:
                    choices.append(
                        app_commands.Choice(
                            name=f"{name} ({staff_id[:12]})",
                            value=staff_id,
                        )
                    )

                if len(choices) >= 25:
                    break

            return choices

        except Exception as exc:
            await send_error_log("Staff Record Autocomplete Error", exc)
            return []

    @leaderboard.autocomplete("scope")
    async def leaderboard_scope_autocomplete(
            self,
            interaction: discord.Interaction,
            current: str,
    ) -> list[app_commands.Choice[str]]:
        try:
            current = str(current or "").lower()

            return [
                app_commands.Choice(name=option, value=option)
                for option in _AUTOCOMPLETE_SCOPES
                if current in option
            ]
        except Exception as exc:
            await send_error_log("Leaderboard Scope Autocomplete Error", exc)
            return []

    @commands.hybrid_command(
        name="reset_monthly_leaderboard",
        description="Reset ONLY monthly leaderboard",
    )
    async def reset_monthly_leaderboard(self, ctx) -> None:
        if not await self._require_level(ctx, LEVEL_OWNER):
            return

        try:
            async with app_state.leaderboard_lock:
                reset_monthly_leaderboard_data()

            await respond(
                ctx,
                embed=owner_embed(
                    "Monthly Reset",
                    "Monthly leaderboard cleared.",
                ),
                ephemeral=True,
            )

        except Exception as exc:
            await self._handle_mod_error(
                ctx,
                "Reset Failed",
                "Monthly Reset Error",
                exc,
            )

    @commands.hybrid_command(
        name="repeatstats",
        description="Show repeat offender stats",
    )
    async def repeatstats(self, ctx) -> None:
        if not await self._require_level(ctx, LEVEL_CAPO):
            return

        try:
            embed = info_embed("Repeat Offender Stats")

            embed.add_field(
                name="Tracked Targets",
                value=str(len(app_state.repeat_offender_actions)),
                inline=True,
            )

            embed.add_field(
                name="Alert Keys",
                value=str(len(app_state.repeat_alerted_keys)),
                inline=True,
            )

            for action, (threshold, window) in _SEVERITY_THRESHOLDS.items():
                embed.add_field(
                    name=f"{action.title()} Threshold",
                    value=f"{threshold} in {window}d",
                    inline=False,
                )

            await respond(ctx, embed=embed, ephemeral=True)

        except Exception as exc:
            await self._handle_mod_error(
                ctx,
                "Repeat Stats Failed",
                "Repeat Stats Command Error",
                exc,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Commands(bot))
