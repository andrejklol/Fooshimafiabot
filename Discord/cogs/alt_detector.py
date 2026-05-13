from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import discord
from discord.ext import commands

log = logging.getLogger(__name__)

ALT_ALERT_CHANNEL_ID = int(os.getenv("ALT_ALERT_CHANNEL_ID", "1503863393667518694"))
ALT_ACCOUNT_AGE_DAYS = int(os.getenv("ALT_ACCOUNT_AGE_DAYS", "30"))


class AltDetector(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        now = datetime.now(timezone.utc)
        account_age = now - member.created_at
        age_days = account_age.days

        if age_days >= ALT_ACCOUNT_AGE_DAYS:
            return

        channel = self.bot.get_channel(ALT_ALERT_CHANNEL_ID)
        if not channel:
            log.warning("[alt_detector] Alert channel %s not found.", ALT_ALERT_CHANNEL_ID)
            return

        # Risk level based on account age
        if age_days <= 3:
            color = 0xFF0000  # red — very new
            risk = "🔴 HIGH"
        elif age_days <= 7:
            color = 0xFF6600  # orange — suspicious
            risk = "🟠 MEDIUM"
        else:
            color = 0xFFFF00  # yellow — worth watching
            risk = "🟡 LOW"

        default_avatar = member.avatar is None

        embed = discord.Embed(
            title="⚠️ Potential Alt Account Detected",
            color=color,
            timestamp=now,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="User", value=f"{member.mention}\n`{member.name}`", inline=True)
        embed.add_field(name="User ID", value=f"`{member.id}`", inline=True)
        embed.add_field(name="Risk Level", value=risk, inline=True)
        embed.add_field(
            name="Account Created",
            value=f"<t:{int(member.created_at.timestamp())}:F>\n({age_days} day(s) ago)",
            inline=True,
        )
        embed.add_field(
            name="Default Avatar",
            value="Yes ⚠️" if default_avatar else "No",
            inline=True,
        )
        embed.add_field(
            name="Joined Server",
            value=f"<t:{int(now.timestamp())}:F>",
            inline=True,
        )
        embed.set_footer(text=f"Account age threshold: {ALT_ACCOUNT_AGE_DAYS} days")

        await channel.send(embed=embed)
        log.info(
            "[alt_detector] Flagged %s (id=%s) — account age %d days.",
            member.name, member.id, age_days,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AltDetector(bot))
