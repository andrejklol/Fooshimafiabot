import logging
from datetime import datetime, timezone

import discord

from core.config import ALERT_CHANNEL_ID

log = logging.getLogger("alerts")


async def send_alert(
    bot,
    title: str,
    description: str,
    level: str = "info",
):

    if not bot:
        return

    try:

        channel = bot.get_channel(ALERT_CHANNEL_ID)

        if not channel:
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

    except Exception as e:
        log.warning("Failed to send alert: %s", e)
