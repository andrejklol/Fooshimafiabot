import logging
import traceback

import discord
from discord.ext import commands

from cogs.commands import Commands, perform_command_sync
from cogs.error_handler import ErrorHandler
from cogs.general import General
from cogs.tasks import TasksCog
from core.cache import app_state
from core.config import SYNC_COMMANDS_ON_STARTUP, TOKEN
from core.logger import setup_logging
from core.utils import send_error_log
from services.leaderboard.service import (
    load_leaderboard_data,
    seed_leaderboards,
)
from services.leaderboard.storage import leaderboard_data
from services.offenders.storage import load_repeat_offenders
from services.vrchat_client import (
    login_vrchat,
    refresh_vrc_group_members,
    set_startup_timestamp,
)

# ============================================================
# LOGGING SETUP
# ============================================================

setup_logging()
logger = logging.getLogger("main")

# ============================================================
# BOT SETUP
# ============================================================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,
)

# ============================================================
# HELPERS
# ============================================================

def _has_existing_leaderboard_data() -> bool:
    return bool(leaderboard_data.get("staff"))

# ============================================================
# STARTUP
# ============================================================

@bot.event
async def setup_hook():
    app_state.bot = bot
    app_state.startup_complete = False

    logger.info("Loading cogs...")

    await bot.add_cog(General(bot))
    await bot.add_cog(Commands(bot))
    await bot.add_cog(TasksCog(bot))
    await bot.add_cog(ErrorHandler(bot))

    logger.info("Cogs loaded.")

    if not SYNC_COMMANDS_ON_STARTUP:
        logger.info("Startup command sync skipped.")
        return

    try:
        result = await perform_command_sync(bot, clear_guild=False)
        logger.info(result)

    except Exception as exc:
        logger.exception("Startup command sync error")
        await send_error_log(
            "Startup Command Sync Error",
            exc,
        )

@bot.event
async def on_ready():
    logger.info("Logged in as %s", bot.user)

    if getattr(app_state, "startup_complete", False):
        logger.info("Startup already completed; skipping duplicate initialization.")
        return

    logger.info("Running startup initialization...")

    try:
        load_leaderboard_data()
        logger.info("Leaderboard loaded.")

    except Exception as exc:
        logger.exception("Leaderboard load error")
        await send_error_log(
            "Leaderboard Load Error",
            exc,
        )

    try:
        load_repeat_offenders()
        logger.info("Repeat offenders loaded.")

    except Exception as exc:
        logger.exception("Repeat offender load error")
        await send_error_log(
            "Repeat Offender Load Error",
            exc,
        )

    try:
        logged_in = await login_vrchat()

        if logged_in:
            logger.info("VRChat login successful.")

            await refresh_vrc_group_members()
            logger.info("VRChat group members refreshed.")

            await set_startup_timestamp()
            logger.info("Startup timestamp set.")

            if not _has_existing_leaderboard_data():
                seeded = await seed_leaderboards()
                logger.info("Leaderboards seeded. Loaded %s entries.", seeded)
            else:
                logger.info("Skipped leaderboard seeding (existing data found).")

        else:
            logger.warning("VRChat login failed; skipping member refresh and seed.")

    except Exception as exc:
        logger.exception("VRChat startup error")
        await send_error_log(
            "VRChat Startup Error",
            exc,
        )

    app_state.startup_complete = True
    logger.info("Startup initialization complete.")

# ============================================================
# GLOBAL EVENT ERROR HANDLING
# ============================================================

@bot.event
async def on_error(event, *args, **kwargs):
    tb = traceback.format_exc()
    logger.exception("Unhandled event error in %s", event)

    await send_error_log(
        f"Event Error: {event}",
        tb,
    )

# ============================================================
# RUN BOT
# ============================================================

if not TOKEN:
    raise ValueError("DISCORD_TOKEN missing in .env")

logger.info("Starting bot...")
bot.run(TOKEN)
