import discord

from core.cache import app_state
from core.config import GUILD_ID, REPEAT_ALERT_CHANNEL_ID, STAFF_ALERT_ORDER
from core.embeds import (
    ban_action_embed,
    kick_action_embed,
    warn_action_embed,
    warning_embed,
)
from core.utils import send_error_log, utc_now
from services.vrchat_client import is_vrchat_user_online


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

    normalized = []

    for entry in rank_entries:

        if isinstance(entry, dict):

            normalized.append({
                "discord_id": entry.get("discord_id"),
                "vrchat_username": entry.get("vrchat_username"),
                "vrchat_user_id": entry.get("vrchat_user_id"),
            })

        else:

            normalized.append({
                "discord_id": entry,
                "vrchat_username": None,
                "vrchat_user_id": None,
            })

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
        return False

    try:

        online = await is_vrchat_user_online(
            vrchat_username=vrchat_username,
            vrchat_user_id=vrchat_user_id,
        )

        return online

    except Exception as exc:

        await send_error_log(
            "Repeat Alert VRChat Check Error",
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
                "Repeat Alert Guild Missing",
                f"GUILD_ID not found: {GUILD_ID}",
            )

            return None, None, None


        rank_groups = STAFF_ALERT_ORDER.get(action_type, [])

        if not rank_groups:
            return None, None, None


        for index in range(start_index, len(rank_groups)):

            rank_name, raw_entries = rank_groups[index]

            entries = normalize_staff_entries(raw_entries)

            candidates = []


            for entry in entries:

                discord_id = entry["discord_id"]
                vrchat_username = entry["vrchat_username"]
                vrchat_user_id = entry["vrchat_user_id"]

                if not discord_id:
                    continue


                member = guild.get_member(discord_id)

                if member is None:

                    try:
                        member = await guild.fetch_member(discord_id)

                    except Exception:
                        member = None


                if member is None:
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

                return chosen, rank_name, index


        return None, None, None


    except Exception as exc:

        await send_error_log(
            "Repeat Alert Pick Staff Error",
            exc,
            extra=f"action_type={action_type}",
        )

        return None, None, None


# ============================================================
# ALERT EMBED BUILDER
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
            for action, count, window, _ in triggered
        ),
        inline=False,
    )


    embed.add_field(
        name="Highest Severity",
        value=highest_action.upper(),
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
            value="No staff currently online in VRChat",
            inline=False,
        )


    embed.set_footer(
        text="Only pings staff currently online in VRChat"
    )


    return embed


# ============================================================
# BUTTON VIEW
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
        label="Handled",
        style=discord.ButtonStyle.success,
        emoji="✅",
    )
    async def handled_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        embed = interaction.message.embeds[0]

        embed.color = discord.Color.green()

        embed.add_field(
            name="Handled By",
            value=interaction.user.mention,
            inline=False,
        )

        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            embed=embed,
            view=self,
        )


    @discord.ui.button(
        label="Escalate",
        style=discord.ButtonStyle.danger,
        emoji="⬆",
    )
    async def escalate_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        next_index = (
            self.current_rank_index + 1
            if self.current_rank_index is not None
            else 0
        )


        member, rank, new_index = await pick_alert_user_for_action(
            self.highest_action,
            start_index=next_index,
        )


        if member is None:

            await interaction.response.send_message(
                "No higher rank staff online",
                ephemeral=True,
            )

            return


        embed = interaction.message.embeds[0]

        embed.color = discord.Color.red()

        embed.add_field(
            name="Escalated To",
            value=f"{member.mention} ({rank})",
            inline=False,
        )

        await interaction.response.edit_message(
            embed=embed,
            view=self,
        )

        await interaction.followup.send(member.mention)


# ============================================================
# SEND ALERT
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
                f"Missing channel {REPEAT_ALERT_CHANNEL_ID}",
            )

            return


        assigned_member, assigned_rank, rank_index = await pick_alert_user_for_action(
            highest_action
        )


        embed = build_repeat_alert_embed(
            pretty_name,
            target_id,
            triggered,
            highest_action,
            assigned_member,
            assigned_rank,
        )


        view = RepeatOffenderView(
            target_id,
            highest_action,
            rank_index,
        )


        await channel.send(
            content=assigned_member.mention if assigned_member else None,
            embed=embed,
            view=view,
            allowed_mentions=discord.AllowedMentions(users=True),
        )


    except Exception as exc:

        await send_error_log(
            "Repeat Alert Send Error",
            exc,
            extra=f"{pretty_name} ({target_id})",
        )
