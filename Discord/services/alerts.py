import logging
from datetime import datetime, timezone

import discord

from core.cache import app_state
from core.config import (
    CHANNELS,
    GUILD_ID,
    HIGH_STAFF_ALERT,
    STAFF_ALERT_ORDER,
)
from core.embeds import (
    ban_action_embed,
    kick_action_embed,
    warn_action_embed,
    warning_embed,
)
from core.utils import send_error_log, utc_now
from services.vrchat import is_vrchat_user_online

log = logging.getLogger("alerts")

# ============================================================
# DASHBOARD SYNC HELPERS
# ============================================================

def _get_dashboard_sync_client(bot=None):
    client = getattr(app_state, "dashboard_sync", None)
    if client: return client
    if bot:
        client = getattr(bot, "dashboard_sync", None)
        if client: return client
        bot_app_state = getattr(bot, "app_state", None)
        if bot_app_state: return getattr(bot_app_state, "dashboard_sync", None)
    return None

async def _push_alert_to_dashboard(*, bot=None, title: str, message: str, alert_type: str = "custom", severity: str = "info"):
    client = _get_dashboard_sync_client(bot)
    if not client or not hasattr(client, "push_alert"): return
    result = await client.push_alert(title=title, message=message, alert_type=alert_type, severity=severity)
    if isinstance(result, dict) and not result.get("success", False):
        await send_error_log("Dashboard Alert Sync Failed", result.get("error", "Unknown error"))

# ============================================================
# GENERIC ALERT
# ============================================================

async def send_alert(bot, title: str, description: str, level: str = "info") -> None:
    try:
        await _push_alert_to_dashboard(bot=bot, title=title, message=description, alert_type="custom", severity=level)
        log.info("[ALERT %s] %s", level.upper(), title)
    except Exception as exc:
        await send_error_log("Alert Sync Error", exc)

# ============================================================
# REPEAT ALERT LOGIC (Restored)
# ============================================================

def get_member_status_rank(status: discord.Status) -> int:
    status_map = {discord.Status.online: 3, discord.Status.idle: 2, discord.Status.dnd: 1}
    return status_map.get(status, 0)

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
            normalized.append({"discord_id": entry, "vrchat_username": None, "vrchat_user_id": None})
    return normalized

async def is_available_for_alert(member: discord.Member, vrchat_username: str | None, vrchat_user_id: str | None) -> bool:
    if not member: return False
    v_name = str(vrchat_username or "").strip() or None
    v_id = str(vrchat_user_id or "").strip() or None
    if not v_name and not v_id: return False
    try:
        return await is_vrchat_user_online(vrchat_username=v_name, vrchat_user_id=v_id)
    except Exception:
        return False

async def pick_alert_user_for_action(action_type: str, start_index: int = 0):
    if not app_state.bot: return None, None, None
    guild = app_state.bot.get_guild(GUILD_ID) or await app_state.bot.fetch_guild(GUILD_ID)
    rank_groups = STAFF_ALERT_ORDER.get(action_type, [])
    for index in range(start_index, len(rank_groups)):
        rank_name, raw_entries = rank_groups[index]
        entries = normalize_staff_entries(raw_entries)
        candidates = []
        for entry in entries:
            if not entry["discord_id"]: continue
            try:
                member = guild.get_member(entry["discord_id"]) or await guild.fetch_member(entry["discord_id"])
                if member and await is_available_for_alert(member, entry["vrchat_username"], entry["vrchat_user_id"]):
                    candidates.append(member)
            except: continue
        if candidates:
            candidates.sort(key=lambda m: get_member_status_rank(m.status), reverse=True)
            return candidates[0], rank_name, index
    return None, None, None

# --- UI View Restored ---
class RepeatOffenderView(discord.ui.View):
    def __init__(self, target_id: str, highest_action: str, current_rank_index: int | None):
        super().__init__(timeout=None)
        self.target_id = target_id
        self.highest_action = highest_action
        self.current_rank_index = current_rank_index

    @discord.ui.button(label="Handled", style=discord.ButtonStyle.success, emoji="✅")
    async def handled_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = interaction.message.embeds[0] if interaction.message.embeds else None
        if not embed: return
        embed.color = discord.Color.green()
        embed.add_field(name="Handled By", value=interaction.user.mention, inline=False)
        for child in self.children: child.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Escalate", style=discord.ButtonStyle.danger, emoji="⬆")
    async def escalate_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        next_idx = (self.current_rank_index + 1) if self.current_rank_index is not None else 0
        member, rank, new_idx = await pick_alert_user_for_action(self.highest_action, start_index=next_idx)
        if not member:
            return await interaction.response.send_message("No higher rank staff online", ephemeral=True)
        self.current_rank_index = new_idx
        embed = interaction.message.embeds[0]
        embed.add_field(name="Escalated To", value=f"{member.mention} ({rank})", inline=False)
        await interaction.response.edit_message(embed=embed, view=self)
        await interaction.followup.send(member.mention)

async def send_repeat_alert(pretty_name: str, target_id: str, triggered: list, highest_action: str):
    """Restored the missing export function!"""
    try:
        msg = f"User: {pretty_name} ({target_id})\nHighest: {highest_action}"
        await _push_alert_to_dashboard(
            bot=app_state.bot, title="Repeat Offender Alert", message=msg,
            alert_type="repeat_offender", severity=highest_action if highest_action in {"warn", "kick", "ban"} else "warning"
        )
        log.info("[REPEAT ALERT] %s (%s)", pretty_name, target_id)
    except Exception as exc:
        await send_error_log("Repeat Alert Sync Error", exc)

# ============================================================
# HIGH STAFF ALERT
# ============================================================

async def send_high_staff_alert(bot, moderator_name: str, action_type: str, count: int, window_minutes: int, threshold: int, discord_user_id: str = None):
    try:
        msg = f"Mod: {moderator_name}\nAction: {action_type}\nCount: {count}\nWindow: {window_minutes}m"
        severity = "error" if action_type.lower() == "ban" else "warning"
        await _push_alert_to_dashboard(bot=bot, title="High Staff Activity", message=msg, alert_type="high_staff", severity=severity)
    except Exception as exc:
        await send_error_log("High Staff Alert Sync Error", exc)
