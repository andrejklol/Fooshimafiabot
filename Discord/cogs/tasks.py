import asyncio
from discord.ext import commands, tasks

from core.config import AUTOSAVE_SECONDS, LOG_POLL_MINUTES
from core.utils import send_error_log
from services.leaderboard.processors import sync_all_vrc_staff_into_leaderboard
from services.tasks import (
    autosave_if_dirty,
    check_logs_once,
    refresh_group_cache_once,
    check_monthly_reset,
    initialize_monthly_reset_state,
)


GROUP_CACHE_REFRESH_MINUTES = 15


class TasksCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        initialize_monthly_reset_state()

        if not self.check_logs_loop.is_running():
            self.check_logs_loop.start()

        if not self.autosave_loop.is_running():
            self.autosave_loop.start()

        if not self.refresh_group_cache_loop.is_running():
            self.refresh_group_cache_loop.start()

        if not self.monthly_reset_loop.is_running():
            self.monthly_reset_loop.start()

    def cog_unload(self):
        if self.check_logs_loop.is_running():
            self.check_logs_loop.cancel()

        if self.autosave_loop.is_running():
            self.autosave_loop.cancel()

        if self.refresh_group_cache_loop.is_running():
            self.refresh_group_cache_loop.cancel()

        if self.monthly_reset_loop.is_running():
            self.monthly_reset_loop.cancel()

    # --------------------------------

    @tasks.loop(minutes=LOG_POLL_MINUTES)
    async def check_logs_loop(self):
        try:
            await check_logs_once()
        except Exception as exc:
            await send_error_log(
                "Task Loop Error",
                exc,
                extra={"loop": "check_logs_loop"},
            )

    # --------------------------------

    @tasks.loop(seconds=AUTOSAVE_SECONDS)
    async def autosave_loop(self):
        try:
            await autosave_if_dirty()
        except Exception as exc:
            await send_error_log(
                "Task Loop Error",
                exc,
                extra={"loop": "autosave_loop"},
            )

    # --------------------------------

    @tasks.loop(minutes=GROUP_CACHE_REFRESH_MINUTES)
    async def refresh_group_cache_loop(self):
        try:
            await refresh_group_cache_once()
            await sync_all_vrc_staff_into_leaderboard(force_refresh=False)
        except Exception as exc:
            await send_error_log(
                "Task Loop Error",
                exc,
                extra={"loop": "refresh_group_cache_loop"},
            )

    # --------------------------------

    @tasks.loop(minutes=1)
    async def monthly_reset_loop(self):
        try:
            await check_monthly_reset()
        except Exception as exc:
            await send_error_log(
                "Task Loop Error",
                exc,
                extra={"loop": "monthly_reset_loop"},
            )

    # --------------------------------

    @check_logs_loop.before_loop
    @autosave_loop.before_loop
    @refresh_group_cache_loop.before_loop
    @monthly_reset_loop.before_loop
    async def before_loops(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(10)


async def setup(bot: commands.Bot):
    await bot.add_cog(TasksCog(bot))