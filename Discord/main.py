from __future__ import annotations
import logging
import traceback
import os
import asyncio
from datetime import datetime, timezone

import discord
from discord.ext import commands

# Core Imports
from core.cache import app_state
from core.config import (
    TOKEN, 
    GUILD_ID, 
    VRC_CONFIG, 
    SYNC_COMMANDS_ON_STARTUP, 
    OWNER_USER_ID
)
from core.logger import setup_logging
from core.utils import send_error_log
from core.reflection_layer import ReflectionLayer

# Cogs & Services
from cogs.error_handler import ErrorHandler
from cogs.tasks import TasksCog

from services.leaderboard.service import load_leaderboard_data, seed_leaderboards
from services.leaderboard.staff_sync import sync_staff_from_vrc_group
from services.leaderboard.storage import leaderboard_data
from services.offenders.storage import ensure_structure, load_repeat_offenders
from services.vrchat import login_vrchat, refresh_vrc_group_members

# Logging Setup
setup_logging()

class RateLimitFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if "rate limited" in record.getMessage().lower():
            return False
        return True

discord_http_logger = logging.getLogger("discord.http")
discord_http_logger.addFilter(RateLimitFilter())

log = logging.getLogger("main")

# ============================================================
# BOT CLASS
# ============================================================

class FooshiBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.reflection_layer: ReflectionLayer | None = None

    async def _init_reflection_layer(self):
        """Initializes the dashboard reflection layer using centralized config."""
        if self.reflection_layer is not None:
            log.info("Reflection Layer already initialised — skipping.")
            return

        log.info("Initializing Reflection Layer components...")

        from services.dashboard_sync import DashboardSync
        
        dashboard_url = VRC_CONFIG.get("dashboard_url") or os.getenv("DASHBOARD_URL", "https://fooshimafia.net")
        dashboard_api_key = VRC_CONFIG.get("dashboard_api_key") or os.getenv("DASHBOARD_API_KEY")

        if not dashboard_api_key:
            log.warning("DASHBOARD_API_KEY not set — reflection layer disabled.")
            return

        sync = DashboardSync(dashboard_url, dashboard_api_key)
        app_state.dashboard_sync = sync

        from core.inbound_client import InboundEventClient
        from core.event_registry import UnifiedEventRegistry

        bot_id = VRC_CONFIG.get("bot_id", "vrc-bot-prod")
        inbound = InboundEventClient(dashboard_sync=sync, actor_resolver=lambda: bot_id)
        registry = UnifiedEventRegistry(sync=sync, inbound_client=inbound)
        app_state.registry = registry

        self.reflection_layer = ReflectionLayer(
            dashboard_sync=sync,
            registry=registry,
            bot_id=bot_id,
        )
        app_state.reflection_layer = self.reflection_layer

        # --- Modules Load ---
        from modules.system_module import SystemModule
        from modules.vrchat_bridge_module import VRChatBridgeModule
        from modules.moderation_module import ModerationModule
        from modules.profiles_module import ProfilesModule

        vrc_bridge = VRChatBridgeModule(
            bot=self,
            vrc_groups_api_getter=lambda: getattr(app_state, "vrc_groups_api", None),
            group_id_getter=lambda: VRC_CONFIG["group_id"],
        )

        profiles = ProfilesModule(
            bot=self,
            guild_id_getter=lambda: GUILD_ID,
            role_id_map_getter=lambda: VRC_CONFIG.get("role_mapping", {}),
            archived_role_id_getter=lambda: VRC_CONFIG.get("archived_role_id"),
        )

        moderation = ModerationModule(
            bot=self,
            guild_id_getter=lambda: GUILD_ID,
            vrchat_bridge=vrc_bridge,
        )

        system = SystemModule(bot=self)

        # Register Modules
        registry.register_module(vrc_bridge)
        registry.register_module(profiles)
        registry.register_module(moderation)
        registry.register_module(system)
        
        app_state.moderation_module = moderation
        app_state.profiles_module = profiles

        await self.reflection_layer.start()
        self.loop.create_task(self._deferred_reconcile(registry))

    async def _deferred_reconcile(self, registry):
        """Waits for the bot data cache to settle before reconciling modules."""
        await self.wait_until_ready()
        await asyncio.sleep(3)
        try:
            results = await registry.reconcile_all()
            log.debug("reconcile_all results: %s", results)
        except Exception:
            log.exception("reconcile_all failed during deferred startup execution")

    async def close(self):
        log.info("Shutting down...")
        if self.reflection_layer is not None:
            try:
                await self.reflection_layer.stop()
            except Exception:
                log.exception("Reflection layer stop failed")
        await super().close()

# ============================================================
# INSTANTIATION
# ============================================================

intents = discord.Intents.default()
intents.message_content = True  
intents.members = True          
intents.presences = True        

bot = FooshiBot(
    command_prefix="!",
    intents=intents,
    help_command=None,
    owner_id=OWNER_USER_ID
)

# ============================================================
# STARTUP HOOKS
# ============================================================

@bot.event
async def setup_hook():
    app_state.bot = bot
    app_state.startup_complete = False
    app_state.startup_timestamp = datetime.now(timezone.utc)

    log.info("Loading core cogs...")
    await bot.add_cog(TasksCog(bot))
    await bot.add_cog(ErrorHandler(bot))

    # --- Load Extensions ---
    extensions = [
        "cogs.presence_warmup",
        "cogs.discord_event_logger",
        "cogs.group_stats_sync",
        "cogs.ai_chat",
        "cogs.alt_detector",
    ]
    
    for ext in extensions:
        try:
            await bot.load_extension(ext)
            log.info(f"Extension loaded: {ext}")
        except Exception as e:
            log.error(f"Failed to load extension {ext}: {e}")

    # --- Load Command Categories ---
    command_groups = ["godfooshi", "underboss", "consigliere", "capo", "soldier", "general"]
    for name in command_groups:
        ext_path = f"cogs.commands.{name}_commands"
        try:
            await bot.load_extension(ext_path)
        except Exception as e:
            log.error(f"Failed to load command extension {ext_path}: {e}")

    # --- Reflection Layer Init ---
    try:
        await bot._init_reflection_layer()
    except Exception as exc:
        log.exception("Reflection layer init failed")

    # --- Service/Storage Init ---
    load_leaderboard_data()
    load_repeat_offenders()
    ensure_structure()

    # --- VRChat Integration Init ---
    try:
        if await login_vrchat():
            await refresh_vrc_group_members()
            await sync_staff_from_vrc_group(force_refresh=False)
            if not bool(leaderboard_data.get("staff")):
                await seed_leaderboards()
    except Exception as exc:
        log.exception("VRChat startup error")

    if SYNC_COMMANDS_ON_STARTUP:
        bot.loop.create_task(sync_application_commands())

async def sync_application_commands():
    await bot.wait_until_ready()
    try:
        target_guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=target_guild)
        synced = await bot.tree.sync(guild=target_guild)
        log.info("Synced %s commands.", len(synced))
    except Exception:
        log.exception("Command sync failed")

@bot.event
async def on_ready():
    log.info(f"Logged in as {bot.user}")
    guild = bot.get_guild(GUILD_ID)
    if guild:
        await guild.chunk()
    app_state.startup_complete = True
    log.info("Bot is fully ready.")

@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return
    await bot.process_commands(message)

if __name__ == "__main__":
    if not TOKEN:
        raise ValueError("DISCORD_TOKEN missing")
    bot.run(TOKEN)
