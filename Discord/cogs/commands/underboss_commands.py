from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from core.embeds import success_embed, warning_embed
from core.utils import respond

from .permissions import check_level, LEVEL_UNDERBOSS

log = logging.getLogger(__name__)

# ============================================================
# GIVEAWAY HELPERS
# ============================================================

GIVEAWAY_EMOJI = "🎉"
GIVEAWAY_COLOR = 0xFF6AC1
GIVEAWAY_FILE = Path(__file__).parent.parent.parent / "data" / "giveaways.json"


def _load_giveaways() -> dict:
    if GIVEAWAY_FILE.exists():
        try:
            return json.loads(GIVEAWAY_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_giveaways(data: dict) -> None:
    try:
        GIVEAWAY_FILE.write_text(json.dumps(data, indent=2))
    except Exception as e:
        log.warning("[giveaway] Could not save: %s", e)


# ============================================================
# COG
# ============================================================

class UnderbossCommands(commands.Cog):
    """Management commands for Underboss-level staff."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active: dict[str, dict] = _load_giveaways()
        self.giveaway_check_loop.start()

    def cog_unload(self):
        self.giveaway_check_loop.cancel()

    # ============================================================
    # EXISTING COMMANDS
    # ============================================================

    @commands.hybrid_command(
        name="cooldown",
        description="Set a message cooldown delay for the current channel (0 to disable)",
    )
    @app_commands.describe(seconds="Delay in seconds (0 to disable)")
    async def cooldown(self, ctx: commands.Context, seconds: int):
        if not await check_level(ctx, LEVEL_UNDERBOSS):
            return

        if not isinstance(ctx.channel, discord.TextChannel):
            return await respond(
                ctx,
                embed=warning_embed("Command Failed", "Slowmode can only be set in text channels."),
                ephemeral=True
            )

        try:
            await ctx.channel.edit(slowmode_delay=seconds)
            status = f"set to {seconds}s" if seconds > 0 else "disabled"
            await respond(
                ctx,
                embed=success_embed("Slowmode Updated", f"Channel slowmode has been {status}."),
                ephemeral=True
            )
        except Exception as exc:
            await respond(ctx, embed=warning_embed("Error", str(exc)), ephemeral=True)

    @commands.hybrid_command(
        name="nickname",
        description="Change a member's nickname (leave nickname blank to reset it)",
    )
    @app_commands.describe(member="The member to edit", nickname="The new nickname (leave blank to reset)")
    async def nickname(self, ctx: commands.Context, member: discord.Member, nickname: str | None = None):
        if not await check_level(ctx, LEVEL_UNDERBOSS):
            return

        try:
            await member.edit(nick=nickname)
            await respond(
                ctx,
                embed=success_embed(
                    "Nickname Updated",
                    f"Changed {member.mention}'s nickname to: **{nickname or 'Original Name'}**"
                ),
                ephemeral=True
            )
        except discord.Forbidden:
            await respond(
                ctx,
                embed=warning_embed("Permission Error", "I cannot edit this user's nickname (Hierarchy issue)."),
                ephemeral=True
            )
        except Exception as exc:
            await respond(ctx, embed=warning_embed("Error", str(exc)), ephemeral=True)

    # ============================================================
    # GIVEAWAY COMMAND
    # ============================================================

    @app_commands.command(name="giveaway", description="Manage giveaways (Underboss+)")
    @app_commands.describe(
        action="start, end, or reroll",
        prize="What are you giving away?",
        duration="How long in minutes",
        winners="Number of winners (default 1)",
        channel="Channel to post in (default: current)",
        message_id="Message ID for end/reroll actions",
    )
    async def giveaway(
        self,
        interaction: discord.Interaction,
        action: str,
        prize: Optional[str] = None,
        duration: Optional[int] = None,
        winners: Optional[int] = 1,
        channel: Optional[discord.TextChannel] = None,
        message_id: Optional[str] = None,
    ):
        ctx = await commands.Context.from_interaction(interaction)
        if not await check_level(ctx, LEVEL_UNDERBOSS):
            return

        action = action.lower().strip()

        if action == "start":
            await self._giveaway_start(interaction, prize, duration, winners, channel)
        elif action == "end":
            await self._giveaway_end(interaction, message_id)
        elif action == "reroll":
            await self._giveaway_reroll(interaction, message_id)
        else:
            await interaction.response.send_message(
                "❌ Invalid action. Use `start`, `end`, or `reroll`.", ephemeral=True
            )

    async def _giveaway_start(
        self,
        interaction: discord.Interaction,
        prize: Optional[str],
        duration: Optional[int],
        winners: int,
        channel: Optional[discord.TextChannel],
    ):
        if not prize:
            return await interaction.response.send_message("❌ You must provide a prize.", ephemeral=True)
        if not duration or duration < 1:
            return await interaction.response.send_message("❌ You must provide a duration in minutes.", ephemeral=True)

        winners = max(1, winners or 1)
        target = channel or interaction.channel
        ends_at = datetime.now(timezone.utc).timestamp() + (duration * 60)

        embed = discord.Embed(
            title=f"{GIVEAWAY_EMOJI} GIVEAWAY {GIVEAWAY_EMOJI}",
            description=(
                f"**Prize:** {prize}\n\n"
                f"React with {GIVEAWAY_EMOJI} to enter!\n\n"
                f"**Ends:** <t:{int(ends_at)}:R>\n"
                f"**Winners:** {winners}\n"
                f"**Hosted by:** {interaction.user.mention}"
            ),
            color=GIVEAWAY_COLOR,
            timestamp=datetime.fromtimestamp(ends_at, tz=timezone.utc),
        )
        embed.set_footer(text="Ends at")

        await interaction.response.send_message(f"✅ Giveaway started in {target.mention}!", ephemeral=True)
        msg = await target.send(embed=embed)
        await msg.add_reaction(GIVEAWAY_EMOJI)

        self.active[str(msg.id)] = {
            "channel_id": target.id,
            "message_id": msg.id,
            "prize": prize,
            "ends_at": ends_at,
            "winners": winners,
            "host_id": interaction.user.id,
            "ended": False,
        }
        _save_giveaways(self.active)
        log.info("[giveaway] Started: prize=%r duration=%dm winners=%d", prize, duration, winners)

    async def _giveaway_end(self, interaction: discord.Interaction, message_id: Optional[str]):
        if not message_id:
            return await interaction.response.send_message("❌ Provide a message ID.", ephemeral=True)
        giveaway = self.active.get(message_id)
        if not giveaway or giveaway.get("ended"):
            return await interaction.response.send_message("❌ No active giveaway with that ID.", ephemeral=True)
        await interaction.response.send_message("⏩ Ending giveaway early...", ephemeral=True)
        await self._conclude(message_id, giveaway)

    async def _giveaway_reroll(self, interaction: discord.Interaction, message_id: Optional[str]):
        if not message_id:
            return await interaction.response.send_message("❌ Provide a message ID.", ephemeral=True)
        giveaway = self.active.get(message_id)
        if not giveaway:
            return await interaction.response.send_message("❌ Giveaway not found.", ephemeral=True)
        if not giveaway.get("ended"):
            return await interaction.response.send_message("❌ Giveaway hasn't ended yet.", ephemeral=True)

        channel = self.bot.get_channel(giveaway["channel_id"])
        if not channel:
            return await interaction.response.send_message("❌ Channel not found.", ephemeral=True)
        try:
            msg = await channel.fetch_message(giveaway["message_id"])
        except discord.NotFound:
            return await interaction.response.send_message("❌ Original message not found.", ephemeral=True)

        new_winners = await self._pick_winners(msg, giveaway["winners"])
        await interaction.response.send_message("🎲 Rerolling...", ephemeral=True)

        if new_winners:
            mentions = ", ".join(w.mention for w in new_winners)
            await channel.send(f"🎉 **Reroll!** New winner(s): {mentions}\nCongratulations on **{giveaway['prize']}**!")
        else:
            await channel.send("❌ Couldn't find valid entries to reroll.")

    # ============================================================
    # INTERNAL HELPERS
    # ============================================================

    async def _pick_winners(self, msg: discord.Message, count: int) -> list[discord.Member]:
        reaction = discord.utils.get(msg.reactions, emoji=GIVEAWAY_EMOJI)
        if not reaction:
            return []
        users = [u async for u in reaction.users() if not u.bot]
        if not users:
            return []
        return random.sample(users, min(count, len(users)))

    async def _conclude(self, message_id: str, giveaway: dict):
        channel = self.bot.get_channel(giveaway["channel_id"])
        if not channel:
            return

        try:
            msg = await channel.fetch_message(giveaway["message_id"])
        except discord.NotFound:
            self.active.pop(message_id, None)
            _save_giveaways(self.active)
            return

        winners = await self._pick_winners(msg, giveaway["winners"])
        giveaway["ended"] = True
        self.active[message_id] = giveaway
        _save_giveaways(self.active)

        if winners:
            mentions = ", ".join(w.mention for w in winners)
            result_text = f"**Winner(s):** {mentions}"
            await channel.send(f"🎉 Congratulations {mentions}! You won **{giveaway['prize']}**!")
        else:
            result_text = "**No valid entries.**"
            await channel.send(f"❌ No one entered the giveaway for **{giveaway['prize']}**.")

        ended_embed = discord.Embed(
            title="🎊 GIVEAWAY ENDED 🎊",
            description=(
                f"**Prize:** {giveaway['prize']}\n\n"
                f"{result_text}\n\n"
                f"**Hosted by:** <@{giveaway['host_id']}>"
            ),
            color=0x808080,
            timestamp=datetime.now(timezone.utc),
        )
        ended_embed.set_footer(text="Giveaway ended")
        try:
            await msg.edit(embed=ended_embed)
        except Exception:
            pass

        log.info("[giveaway] Concluded %s prize=%r", message_id, giveaway["prize"])

    # ============================================================
    # BACKGROUND LOOP
    # ============================================================

    @tasks.loop(seconds=15)
    async def giveaway_check_loop(self):
        now = datetime.now(timezone.utc).timestamp()
        to_end = [
            (mid, g) for mid, g in self.active.items()
            if not g.get("ended") and now >= g["ends_at"]
        ]
        for mid, giveaway in to_end:
            try:
                await self._conclude(mid, giveaway)
            except Exception:
                log.exception("[giveaway] Error concluding %s", mid)

    @giveaway_check_loop.before_loop
    async def before_giveaway_check_loop(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UnderbossCommands(bot))