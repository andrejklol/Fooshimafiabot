import logging
import asyncio
import time
import random
import json
import re
import unicodedata
import traceback
import discord
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Union

# Setup logging
logger = logging.getLogger("VRBot.Utils")

# --- DISCORD FORMATTING HELPERS ---

def format_dt(dt: datetime, style: str = 'R') -> str:
    """
    Wraps discord.utils.format_dt to provide dynamic Discord timestamps.
    Styles: 'R' (Relative), 'F' (Long Date/Time), 'd' (Short Date), etc.
    """
    return discord.utils.format_dt(dt, style=style)

# --- TIME & DATE HELPERS ---

def utc_now() -> datetime:
    """Returns the current UTC time with timezone info."""
    return datetime.now(timezone.utc)

def format_remaining_cooldown(seconds: float) -> str:
    """Converts seconds into human-readable HH:MM:SS string."""
    if seconds <= 0:
        return "0s"
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {seconds}s"
    elif minutes > 0:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"

# --- DISCORD INTERACTION HELPERS ---

async def respond(ctx: Any, content: str = None, embed: discord.Embed = None, ephemeral: bool = False):
    """Unified response helper for Slash and Prefix commands."""
    try:
        if hasattr(ctx, "respond"):
            return await ctx.respond(content=content, embed=embed, ephemeral=ephemeral)
        elif hasattr(ctx, "send"):
            return await ctx.send(content=content, embed=embed)
        elif hasattr(ctx, "followup"):
            return await ctx.followup.send(content=content, embed=embed, ephemeral=ephemeral)
    except Exception as e:
        logger.error(f"Failed to send response: {e}")

# --- VRC USER HELPERS (FALLBACK) ---

async def get_vrc_user(user_id: str, app_state: Any):
    """Placeholder helper often imported by soldier/moderation commands."""
    if hasattr(app_state, 'vrc_api'):
        # This assumes your app_state has the VRC client initialized
        return await run_blocking(app_state.vrc_api.get_user, user_id)
    return None

# --- RATE LIMIT HANDLER ---

class RateLimitHandler:
    def __init__(self):
        self.cooldown_until = 0
        self.backoff_count = 0

    def is_active(self) -> bool:
        return time.time() < self.cooldown_until

    def get_remaining(self) -> float:
        return max(0, self.cooldown_until - time.time())

    def mark_failure(self):
        self.backoff_count += 1
        delay = min(300, (2 ** self.backoff_count)) + random.uniform(0, 1)
        self.cooldown_until = time.time() + delay
        logger.warning(f"Rate limit hit. Cooling down for {delay:.2f}s")

    def mark_success(self):
        self.backoff_count = 0
        self.cooldown_until = 0

rl_handler = RateLimitHandler()

def vrchat_cooldown_active() -> bool:
    return rl_handler.is_active()

# --- LOGGING & CORE UTILS ---

async def send_error_log(description: str, error: Exception = None, ctx: Any = None):
    tb = traceback.format_exc() if error else "No traceback available."
    logger.error(f"[ERROR] {description}\n{tb}")

def clean_display_name(name: str) -> str:
    if not name:
        return "Unknown User"
    name = unicodedata.normalize('NFKD', name)
    return "".join(c for c in name if c.isprintable())

async def run_blocking(func, *args, **kwargs):
    while rl_handler.is_active():
        await asyncio.sleep(rl_handler.get_remaining())
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, lambda: func(*args, **kwargs))
        rl_handler.mark_success()
        return result
    except Exception as e:
        if "429" in str(e):
            rl_handler.mark_failure()
        raise e
