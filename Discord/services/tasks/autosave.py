import asyncio
import logging

from core.cache import app_state

from services.leaderboard.storage import save_leaderboard_data
from services.offenders.storage import save_repeat_offenders


log = logging.getLogger("autosave")


async def autosave_if_dirty() -> None:

    if not app_state.leaderboard_dirty:
        return

    if not app_state.leaderboard_lock:
        log.warning("autosave skipped: leaderboard_lock missing")
        return

    async with app_state.leaderboard_lock:

        if not app_state.leaderboard_dirty:
            return

        await asyncio.to_thread(save_leaderboard_data)
        await asyncio.to_thread(save_repeat_offenders)

        app_state.leaderboard_dirty = False

        log.info("autosaved leaderboard + offenders")