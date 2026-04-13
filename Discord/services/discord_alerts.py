import logging
from datetime import datetime, timezone

import discord

from core.config import (
    ALERT_CHANNEL_ID,
    HIGH_STAFF_ALERT_CHANNEL_ID,
)
from core.embeds import high_staff_alert_embed

log = logging.getLogger("discord_alerts")


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
# HIGH STAFF ALERT
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
