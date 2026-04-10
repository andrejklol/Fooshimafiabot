from discord import app_commands
from discord.ext import commands

from core.config import ERROR_LOG_CHANNEL_ID
from core.error_embed import send_exception_embed


class ErrorHandler(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._original_tree_on_error = bot.tree.on_error
        bot.tree.on_error = self.on_app_command_error

    async def cog_unload(self):
        self.bot.tree.on_error = self._original_tree_on_error

    @commands.Cog.listener()
    async def on_command_error(
            self,
            ctx: commands.Context,
            error: Exception,
    ):
        ignored = (
            commands.CommandNotFound,
            commands.CheckFailure,
            commands.DisabledCommand,
        )

        if isinstance(error, ignored):
            return

        await send_exception_embed(
            self.bot,
            ERROR_LOG_CHANNEL_ID,
            title="Prefix Command Error",
            error=error,
            username=str(ctx.author) if ctx.author else "Unknown",
            actor_id=str(ctx.author.id) if ctx.author else "unknown",
            extra={
                "command": getattr(ctx.command, "qualified_name", "unknown"),
                "message": ctx.message.content if ctx.message else None,
                "guild": getattr(ctx.guild, "name", "DM") if ctx.guild else "DM",
                "channel_id": getattr(ctx.channel, "id", "unknown"),
            },
        )

    async def on_app_command_error(
            self,
            interaction,
            error: app_commands.AppCommandError,
    ):
        command = getattr(interaction, "command", None)
        command_name = (
                getattr(command, "qualified_name", None)
                or getattr(command, "name", None)
                or "unknown"
        )

        user = getattr(interaction, "user", None)
        guild = getattr(interaction, "guild", None)
        channel = getattr(interaction, "channel", None)

        await send_exception_embed(
            self.bot,
            ERROR_LOG_CHANNEL_ID,
            title="Slash Command Error",
            error=error,
            username=str(user) if user else "Unknown",
            actor_id=str(user.id) if user else "unknown",
            extra={
                "command": command_name,
                "guild": getattr(guild, "name", "DM") if guild else "DM",
                "channel_id": getattr(channel, "id", "unknown"),
            },
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ErrorHandler(bot))