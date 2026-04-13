import discord
from datetime import UTC, datetime


# ============================================================
# BASE
# ============================================================

def base_embed(
        title: str,
        description: str | None = None,
        color: int = 0x2B2D31,
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(UTC),
    )
    embed.set_footer(text="VRChat Moderation Bot")
    return embed


# ============================================================
# STANDARD TYPES
# ============================================================

def success_embed(title: str, description: str | None = None) -> discord.Embed:
    return base_embed(f"✅ {title}", description, 0x57F287)


def error_embed(title: str, description: str | None = None) -> discord.Embed:
    return base_embed(f"❌ {title}", description, 0xED4245)


def warning_embed(title: str, description: str | None = None) -> discord.Embed:
    return base_embed(f"⚠️ {title}", description, 0xFEE75C)


def info_embed(title: str, description: str | None = None) -> discord.Embed:
    return base_embed(f"ℹ️ {title}", description, 0x5865F2)


def owner_embed(title: str, description: str | None = None) -> discord.Embed:
    return base_embed(f"👑 {title}", description, 0x9B59B6)


# ============================================================
# MODERATION COLORS
# ============================================================

def warn_action_embed(title: str, description: str | None = None) -> discord.Embed:
    return base_embed(f"🟨 {title}", description, 0xF1C40F)


def kick_action_embed(title: str, description: str | None = None) -> discord.Embed:
    return base_embed(f"🟧 {title}", description, 0xE67E22)


def ban_action_embed(title: str, description: str | None = None) -> discord.Embed:
    return base_embed(f"🟥 {title}", description, 0xE74C3C)


def leaderboard_embed(title: str, description: str | None = None) -> discord.Embed:
    return base_embed(f"🏆 {title}", description, 0xFFD700)


# ============================================================
# HIGH STAFF ALERT EMBED
# ============================================================

def high_staff_alert_embed(
        moderator_name: str,
        action_type: str,
        count: int,
        window_minutes: int,
        threshold: int,
        vrchat_user_id: str | None = None,
) -> discord.Embed:
    pretty_action = action_type.upper()

    embed = base_embed(
        f"🚨 High Staff Activity Detected",
        color=0xFF4D4D,
    )

    embed.add_field(name="Moderator", value=moderator_name, inline=True)
    embed.add_field(name="Action", value=pretty_action, inline=True)
    embed.add_field(name="Count", value=str(count), inline=True)
    embed.add_field(name="Window", value=f"{window_minutes} minutes", inline=True)
    embed.add_field(name="Threshold", value=str(threshold), inline=True)

    if vrchat_user_id:
        embed.add_field(name="VRChat User ID", value=vrchat_user_id, inline=False)

    embed.description = (
        f"Detected **{count} {action_type}(s)** within **{window_minutes} minutes**."
    )

    return embed


# ============================================================
# HELPERS
# ============================================================

def add_stat_field(
        embed: discord.Embed,
        name: str,
        value: str | int | float,
        inline: bool = True,
) -> discord.Embed:
    embed.add_field(name=name, value=str(value), inline=inline)
    return embed


def add_user_action_fields(
        embed: discord.Embed,
        moderator_name: str | None = None,
        target_name: str | None = None,
        action: str | None = None,
        count: str | int | None = None,
        reason: str | None = None,
) -> discord.Embed:
    if moderator_name:
        embed.add_field(name="Moderator", value=moderator_name, inline=True)

    if target_name:
        embed.add_field(name="Target", value=target_name, inline=True)

    if action:
        embed.add_field(name="Action", value=action, inline=True)

    if count is not None:
        embed.add_field(name="Count", value=str(count), inline=True)

    if reason:
        embed.add_field(name="Reason", value=reason, inline=False)

    return embed


def set_bot_footer(
        embed: discord.Embed,
        bot_user: discord.ClientUser | None = None,
) -> discord.Embed:
    if bot_user:
        try:
            embed.set_footer(
                text="VRChat Moderation Bot",
                icon_url=bot_user.display_avatar.url,
            )
            return embed
        except Exception:
            pass

    embed.set_footer(text="VRChat Moderation Bot")
    return embed
