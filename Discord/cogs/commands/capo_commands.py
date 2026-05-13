from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from core.config import GUILD_ID
from core.embeds import success_embed, warning_embed
from core.utils import respond

from .permissions import check_level, LEVEL_CAPO

log = logging.getLogger(__name__)

PURGE_LOG_CHANNEL_ID = 1503863393667518694
LARGE_PURGE_THRESHOLD = 100


class PurgeConfirmView(discord.ui.View):
    def __init__(self, author: discord.Member, amount: int, target: Optional[discord.Member]):
        super().__init__(timeout=30)
        self.author = author
        self.amount = amount
        self.target = target
        self.confirmed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("This is not your confirmation.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="I Agree", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            embed=discord.Embed(description="⏳ Purging messages...", color=0xFFAA00),
            view=self,
        )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            embed=discord.Embed(description="❌ Purge cancelled.", color=0xFF0000),
            view=self,
        )
        self.stop()


class CapoCommands(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(
        name="purge",
        description="Bulk delete messages from the channel, optionally filtered to one user",
    )
    @app_commands.describe(
        amount="Number of messages to delete",
        user="Only delete messages from this user (optional)",
    )
    async def purge(
        self,
        ctx: commands.Context,
        amount: int,
        user: Optional[discord.Member] = None,
    ):
        if not await check_level(ctx, LEVEL_CAPO):
            return

        if amount <= 0:
            return await respond(
                ctx,
                embed=warning_embed("Invalid Amount", "Amount must be above 0."),
                ephemeral=True,
            )

        if not ctx.guild or not isinstance(ctx.channel, discord.TextChannel):
            return await respond(
                ctx,
                embed=warning_embed("Purge Failed", "Must be used in a server text channel."),
                ephemeral=True,
            )

        if amount >= LARGE_PURGE_THRESHOLD:
            view = PurgeConfirmView(ctx.author, amount, user)
            target_str = f" from {user.mention}" if user else ""
            confirm_embed = discord.Embed(
                title="⚠️ Large Purge Confirmation",
                description=(
                    f"You are about to delete **{amount} messages**{target_str} in {ctx.channel.mention}.\n\n"
                    f"Click **I Agree** to confirm or **Cancel** to abort."
                ),
                color=0xFF6600,
            )

            if ctx.interaction:
                await ctx.interaction.response.send_message(embed=confirm_embed, view=view, ephemeral=True)
            else:
                await ctx.send(embed=confirm_embed, view=view)

            await view.wait()

            if not view.confirmed:
                return
        else:
            if ctx.interaction:
                await ctx.interaction.response.defer(ephemeral=True)

        check = (lambda m: m.author.id == user.id) if user else None

        try:
            deleted = await self._paginated_purge(ctx.channel, amount, check)

            result_embed = success_embed(
                "Messages Deleted",
                f"Deleted **{deleted}** message(s)"
                + (f" from {user.mention}" if user else "")
                + f" in {ctx.channel.mention}."
            )

            if ctx.interaction:
                await ctx.interaction.followup.send(embed=result_embed, ephemeral=True)
            else:
                await ctx.send(embed=result_embed, delete_after=5)

            if amount >= LARGE_PURGE_THRESHOLD:
                await self._log_purge(ctx, deleted, user)

        except discord.Forbidden:
            await respond(ctx, embed=warning_embed("Purge Failed", "I don't have 'Manage Messages' permission."), ephemeral=True)
        except Exception as exc:
            await respond(ctx, embed=warning_embed("Purge Failed", str(exc)[:200]), ephemeral=True)

    async def _paginated_purge(self, channel: discord.TextChannel, amount: int, check=None) -> int:
        deleted_total = 0
        remaining = amount

        while remaining > 0:
            batch = min(remaining, 100)
            if check:
                deleted = await channel.purge(limit=batch * 3, check=check, bulk=True)
            else:
                deleted = await channel.purge(limit=batch, bulk=True)

            count = len(deleted)
            deleted_total += count

            if count == 0:
                break

            remaining -= count
            if remaining > 0:
                await asyncio.sleep(1)

        return deleted_total

    async def _log_purge(self, ctx: commands.Context, deleted: int, target: Optional[discord.Member]):
        log_channel = self.bot.get_channel(PURGE_LOG_CHANNEL_ID)
        if not log_channel:
            return

        embed = discord.Embed(
            title="🗑️ Large Purge Executed",
            color=0xFF6600,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Staff", value=ctx.author.mention, inline=True)
        embed.add_field(name="Channel", value=ctx.channel.mention, inline=True)
        embed.add_field(name="Messages Deleted", value=str(deleted), inline=True)
        if target:
            embed.add_field(name="Filtered To", value=target.mention, inline=True)

        await log_channel.send(embed=embed)

    @commands.hybrid_command(
        name="kick",
        description="Remove a member from the server (they can rejoin)",
    )
    @app_commands.describe(user="The member to kick", reason="Reason for the kick")
    async def kick(
        self,
        ctx: commands.Context,
        user: discord.Member,
        *,
        reason: str = "No reason provided",
    ):
        if not await check_level(ctx, LEVEL_CAPO):
            return

        if user.id == ctx.author.id:
            return await respond(ctx, embed=warning_embed("Kick Failed", "You cannot kick yourself."), ephemeral=True)

        try:
            await user.kick(reason=f"Action by {ctx.author}: {reason}")
            await respond(
                ctx,
                embed=success_embed("User Kicked", f"**User:** {user.mention}\n**Reason:** {reason}"),
                ephemeral=True,
            )
        except discord.Forbidden:
            await respond(ctx, embed=warning_embed("Kick Failed", "I do not have permission to kick this user (Role Hierarchy)."), ephemeral=True)
        except Exception as exc:
            await respond(ctx, embed=warning_embed("Kick Failed", str(exc)[:200]), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(CapoCommands(bot))
