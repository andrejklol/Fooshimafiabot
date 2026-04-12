import asyncio
import logging

from core.cache import app_state

from services.leaderboard.storage import save_leaderboard_data
from services.offenders.storage import save_repeat_offenders


log = logging.getLogger("autosave")


async def _run_saves():
    await asyncio.to_thread(save_leaderboard_data)
    await asyncio.to_thread(save_repeat_offenders)


async def autosave_if_dirty() -> None:
    """
    Saves leaderboard + repeat offender data if marked dirty.

    Protected by async lock to prevent concurrent writes.
    Safe to call frequently.
    """

    if not app_state.leaderboard_dirty:
        return

    lock = getattr(app_state, "leaderboard_lock", None)

    if lock is None:
        log.warning("autosave skipped: leaderboard_lock missing")
        return

    async with lock:

        # double-check after acquiring lock
        if not app_state.leaderboard_dirty:
            return

        await _run_saves()

        app_state.leaderboard_dirty = False

        log.info("autosaved leaderboard + offenders")
