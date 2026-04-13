import logging
from datetime import datetime, timezone

import discord

from core.cache import app_state
from core.config import (
    ALERT_CHANNEL_ID,
    GUILD_ID,
    HIGH_STAFF_ALERT_CHANNEL_ID,
    REPEAT_ALERT_CHANNEL_ID,
    STAFF_ALERT_ORDER,
)
from core.embeds import (
    ban_action_embed,
    high_staff_alert_embed,
    kick_action_embed,
    warn_action_embed,
    warning_embed,
)
from core.utils import send_error_log, utc_now
from services.vrchat_client import is_vrchat_user_online

log = logging.getLogger("alerts")


# ============================================================
# GENERIC ALERT
# ============================================================

async def send_alert(
    bot,
    title: str,
    description: str,
    level: str = "info",
) -> None:
    if not bot:
        return

    try:
        channel = bot.get_channel(ALERT_CHANNEL_ID)

        if channel is None:
            try:
                channel = await bot.fetch_channel(ALERT_CHANNEL_ID)
            except Exception:
                channel = None

        if channel is None:
            log.warning("Alert channel not found: %s", ALERT_CHANNEL_ID)
            return

        colors = {
            "info": discord.Color.blue(),
            "warning": discord.Color.orange(),
            "danger": discord.Color.red(),
            "success": discord.Color.green(),
        }

        embed = discord.Embed(
            title=f"🚨 {title}",
            description=description,
            color=colors.get(level, discord.Color.blurple()),
            timestamp=datetime.now(timezone.utc),
        )

        await channel.send(embed=embed)

    except Exception as exc:
        log.warning("Failed to send alert: %r", exc)


# ============================================================
# STATUS PRIORITY
# ============================================================

def get_member_status_rank(status: discord.Status) -> int:
    if status == discord.Status.online:
        return 3
    if status == discord.Status.idle:
        return 2
    if status == discord.Status.dnd:
        return 1
    return 0


# ============================================================
# CONFIG NORMALIZATION
# ============================================================

def normalize_staff_entries(rank_entries):
    """
    Supports BOTH config styles:

    OLD:
        ("Fooshi Capo", [123, 456])

    NEW:
        (
            "Fooshi Capo",
            [
                {
                    "discord_id": 123,
                    "vrchat_user_id": "usr_xxx"
                }
            ]
        )
    """
    normalized = []

    for entry in rank_entries:
        if isinstance(entry, dict):
            normalized.append(
                {
                    "discord_id": entry.get("discord_id"),
                    "vrchat_username": entry.get("vrchat_username"),
                    "vrchat_user_id": entry.get("vrchat_user_id"),
                }
            )
        else:
            normalized.append(
                {
                    "discord_id": entry,
                    "vrchat_username": None,
                    "vrchat_user_id": None,
                }
            )

    return normalized


# ============================================================
# VRCHAT AVAILABILITY CHECK
# ============================================================

async def is_available_for_alert(
    member: discord.Member,
    vrchat_username: str | None,
    vrchat_user_id: str | None,
) -> bool:
    if member is None:
        return False

    vrchat_username = str(vrchat_username or "").strip() or None
    vrchat_user_id = str(vrchat_user_id or "").strip() or None

    if not vrchat_username and not vrchat_user_id:
        print(f"[repeat-alert] skip {member} -> no VRChat identity configured")
        return False

    try:
        online = await is_vrchat_user_online(
            vrchat_username=vrchat_username,
            vrchat_user_id=vrchat_user_id,
        )

        print(
            f"[repeat-alert] {member} | "
            f"username={vrchat_username} | "
            f"id={vrchat_user_id} | "
            f"online={online}"
        )

        return online

    except Exception as exc:
        print(
            f"[repeat-alert] error checking {member}: "
            f"{type(exc).__name__}: {exc}"
        )

        await send_error_log(
            "Alert Availability Check Error",
            exc,
            extra={
                "member": str(member),
                "vrchat_username": vrchat_username,
                "vrchat_user_id": vrchat_user_id,
            },
        )
        return False


# ============================================================
# PICK STAFF MEMBER
# ============================================================

async def pick_alert_user_for_action(
    action_type: str,
    start_index: int = 0,
):
    try:
        if app_state.bot is None:
            return None, None, None

        guild = app_state.bot.get_guild(GUILD_ID)

        if guild is None:
            try:
                guild = await app_state.bot.fetch_guild(GUILD_ID)
            except Exception:
                guild = None

        if guild is None:
            await send_error_log(
                "Alert Guild Missing",
                f"Could not find guild {GUILD_ID}",
                extra=f"action_type={action_type}",
            )
            return None, None, None

        rank_groups = STAFF_ALERT_ORDER.get(action_type, [])

        if not rank_groups:
            return None, None, None

        for index in range(start_index, len(rank_groups)):
            rank_name, raw_entries = rank_groups[index]

            print(f"[repeat-alert] checking rank: {rank_name}")

            entries = normalize_staff_entries(raw_entries)
            candidates = []

            for entry in entries:
                discord_id = entry["discord_id"]
                vrchat_username = entry["vrchat_username"]
                vrchat_user_id = entry["vrchat_user_id"]

                print(
                    "[repeat-alert] candidate -> "
                    f"discord_id={discord_id} "
                    f"vrchat_username={vrchat_username} "
                    f"vrchat_user_id={vrchat_user_id}"
                )

                if not discord_id:
                    continue

                member = guild.get_member(discord_id)

                if member is None:
                    try:
                        member = await guild.fetch_member(discord_id)
                    except Exception:
                        member = None

                if member is None:
                    print(f"[repeat-alert] discord member not found: {discord_id}")
                    continue

                if await is_available_for_alert(
                    member,
                    vrchat_username,
                    vrchat_user_id,
                ):
                    candidates.append(member)

            if candidates:
                candidates.sort(
                    key=lambda m: get_member_status_rank(m.status),
                    reverse=True,
                )

                chosen = candidates[0]

                print(f"[repeat-alert] selected {chosen} from rank {rank_name}")
                return chosen, rank_name, index

        print("[repeat-alert] no online staff found")
        return None, None, None

    except Exception as exc:
        await send_error_log(
            "Pick Alert User Error",
            exc,
            extra=f"action_type={action_type} | start_index={start_index}",
        )
        return None, None, None


# ============================================================
# REPEAT ALERT EMBED BUILDER
# ============================================================

def build_repeat_alert_embed(
    pretty_name: str,
    target_id: str,
    triggered: list[tuple[str, int, int, int]],
    highest_action: str,
    assigned_member: discord.Member | None = None,
    assigned_rank: str | None = None,
) -> discord.Embed:
    highest_action = str(highest_action or "").lower().strip()

    if highest_action == "warn":
        embed = warn_action_embed("Repeat Offender Alert")
    elif highest_action == "kick":
        embed = kick_action_embed("Repeat Offender Alert")
    elif highest_action == "ban":
        embed = ban_action_embed("Repeat Offender Alert")
    else:
        embed = warning_embed("Repeat Offender Alert")

    embed.timestamp = utc_now()

    embed.add_field(
        name="User",
        value=f"**{pretty_name}**\n`{target_id}`",
        inline=False,
    )

    embed.add_field(
        name="Recent Actions",
        value="\n".join(
            f"{action.upper()} x{count} ({window}d)"
            for action, count, window, _threshold in triggered
        ),
        inline=False,
    )

    embed.add_field(
        name="Highest Severity",
        value=highest_action.upper() if highest_action else "UNKNOWN",
        inline=True,
    )

    if assigned_member:
        embed.add_field(
            name="Assigned To",
            value=f"{assigned_member.mention} ({assigned_rank})",
            inline=False,
        )
    else:
        embed.add_field(
            name="Assigned To",
            value="No linked staff online in VRChat",
            inline=False,
        )

    embed.set_footer(text="Only pings staff who are online in VRChat")
    return embed


# ============================================================
# REPEAT ALERT BUTTON VIEW
# ============================================================

class RepeatOffenderView(discord.ui.View):
    def __init__(
        self,
        target_id: str,
        highest_action: str,
        current_rank_index: int | None,
    ):
        super().__init__(timeout=None)
        self.target_id = target_id
        self.highest_action = highest_action
        self.current_rank_index = current_rank_index

    @discord.ui.button(
        label="I handled this",
        style=discord.ButtonStyle.success,
        emoji="✅",
    )
    async def handled_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        embed = (
            interaction.message.embeds[0]
            if interaction.message and interaction.message.embeds
            else None
        )

        if embed is None:
            await interaction.response.send_message(
                "Could not update this alert.",
                ephemeral=True,
            )
            return

        embed.color = discord.Color.green()

        existing_fields = [field.name for field in embed.fields]

        if "Handled By" not in existing_fields:
            embed.add_field(
                name="Handled By",
                value=interaction.user.mention,
                inline=False,
            )

        embed.set_footer(text="Handled")

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            embed=embed,
            view=self,
        )

    @discord.ui.button(
        label="Move to higher rank",
        style=discord.ButtonStyle.danger,
        emoji="❌",
    )
    async def escalate_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        embed = (
            interaction.message.embeds[0]
            if interaction.message and interaction.message.embeds
            else None
        )

        if embed is None:
            await interaction.response.send_message(
                "Could not update this alert.",
                ephemeral=True,
            )
            return

        next_start_index = 0

        if self.current_rank_index is not None:
            next_start_index = self.current_rank_index + 1

        next_member, next_rank, next_rank_index = await pick_alert_user_for_action(
            self.highest_action,
            start_index=next_start_index,
        )

        if next_member is None:
            await interaction.response.send_message(
                "No higher-rank staff member is currently online in VRChat.",
                ephemeral=True,
            )
            return

        self.current_rank_index = next_rank_index
        embed.color = discord.Color.red()

        embed.add_field(
            name="Escalated By",
            value=interaction.user.mention,
            inline=False,
        )

        embed.add_field(
            name="Escalated To",
            value=f"{next_member.mention} ({next_rank})",
            inline=False,
        )

        embed.set_footer(text="Escalated to higher rank")

        await interaction.response.edit_message(
            embed=embed,
            view=self,
        )

        await interaction.followup.send(
            content=next_member.mention,
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item,
    ):
        await send_error_log(
            "RepeatOffenderView Error",
            error,
            extra=(
                f"target_id={self.target_id} | "
                f"highest_action={self.highest_action} | "
                f"item={getattr(item, 'label', type(item).__name__)}"
            ),
        )

        if interaction.response.is_done():
            await interaction.followup.send(
                "Something went wrong handling this alert.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "Something went wrong handling this alert.",
                ephemeral=True,
            )


# ============================================================
# SEND REPEAT ALERT
# ============================================================

async def send_repeat_alert(
    pretty_name: str,
    target_id: str,
    triggered: list[tuple[str, int, int, int]],
    highest_action: str,
):
    try:
        if app_state.bot is None:
            return

        channel = app_state.bot.get_channel(REPEAT_ALERT_CHANNEL_ID)

        if channel is None:
            try:
                channel = await app_state.bot.fetch_channel(REPEAT_ALERT_CHANNEL_ID)
            except Exception:
                channel = None

        if channel is None:
            await send_error_log(
                "Repeat Alert Channel Missing",
                f"Could not find REPEAT_ALERT_CHANNEL_ID {REPEAT_ALERT_CHANNEL_ID}",
                extra=f"target_id={target_id} | highest_action={highest_action}",
            )
            return

        assigned_member, assigned_rank, assigned_rank_index = await pick_alert_user_for_action(
            highest_action
        )

        embed = build_repeat_alert_embed(
            pretty_name=pretty_name,
            target_id=target_id,
            triggered=triggered,
            highest_action=highest_action,
            assigned_member=assigned_member,
            assigned_rank=assigned_rank,
        )

        content = assigned_member.mention if assigned_member else None
        allowed_mentions = discord.AllowedMentions(users=True)

        view = RepeatOffenderView(
            target_id=target_id,
            highest_action=highest_action,
            current_rank_index=assigned_rank_index,
        )

        await channel.send(
            content=content,
            embed=embed,
            view=view,
            allowed_mentions=allowed_mentions,
        )

    except Exception as exc:
        await send_error_log(
            "Send Repeat Alert Error",
            exc,
            extra=(
                f"pretty_name={pretty_name} | "
                f"target_id={target_id} | "
                f"highest_action={highest_action}"
            ),
        )


# ============================================================
# SEND HIGH STAFF ALERT
# ============================================================

async def send_high_staff_alert(
    bot,
    moderator_name: str,
    action_type: str,
    count: int,
    window_minutes: int,
    threshold: int,
    vrchat_user_id: str | None = None,
) -> None:
    if not bot:
        return

    try:
        channel = bot.get_channel(HIGH_STAFF_ALERT_CHANNEL_ID)

        if channel is None:
            try:
                channel = await bot.fetch_channel(HIGH_STAFF_ALERT_CHANNEL_ID)
            except Exception:
                channel = None

        if channel is None:
            log.warning(
                "High staff alert channel not found: %s",
                HIGH_STAFF_ALERT_CHANNEL_ID,
            )
            return

        embed = high_staff_alert_embed(
            moderator_name=moderator_name,
            action_type=action_type,
            count=count,
            window_minutes=window_minutes,
            threshold=threshold,
            vrchat_user_id=vrchat_user_id,
        )

        await channel.send(embed=embed)

        log.info(
            "high staff alert sent | mod=%s action=%s count=%s",
            moderator_name,
            action_type,
            count,
        )

    except Exception as exc:
        log.warning("Failed to send high staff alert: %r", exc)
