import logging
import discord
from discord.ext import commands

log = logging.getLogger("cmd_perms")

LEVEL_USER       = 0
LEVEL_SOLDIER    = 1
LEVEL_CAPO       = 2
LEVEL_CONSIGLIERE = 3
LEVEL_UNDERBOSS  = 4
LEVEL_GODFOOSHI  = 5

LEVEL_NAMES = {
    LEVEL_USER:        "USER",
    LEVEL_SOLDIER:     "SOLDIER",
    LEVEL_CAPO:        "CAPO",
    LEVEL_CONSIGLIERE: "CONSIGLIERE",
    LEVEL_UNDERBOSS:   "UNDERBOSS",
    LEVEL_GODFOOSHI:   "GODFOOSHI",
}

ROLE_LEVEL_MAP = {
    "godfooshi":   LEVEL_GODFOOSHI,
    "underboss":   LEVEL_UNDERBOSS,
    "consigliere": LEVEL_CONSIGLIERE,
    "capo":        LEVEL_CAPO,
    "soldier":     LEVEL_SOLDIER,
}


def get_level_name(level: int) -> str:
    return LEVEL_NAMES.get(level, str(level))

def get_command_name(ctx: commands.Context) -> str:
    return ctx.command.qualified_name if ctx.command else "unknown"

def get_location_names(ctx: commands.Context) -> tuple[str, str]:
    if not ctx.guild:
        return "DM", "DM"
    return ctx.guild.name, getattr(ctx.channel, "name", "unknown")


def get_level(member: discord.Member | discord.User, bot: commands.Bot) -> int:
    if bot.owner_id == member.id or member.id in (bot.owner_ids or []):
        return LEVEL_GODFOOSHI

    if not isinstance(member, discord.Member):
        return LEVEL_USER

    highest_level = LEVEL_USER
    for role in member.roles:
        name_low = role.name.lower()
        for keyword, level in ROLE_LEVEL_MAP.items():
            if keyword in name_low:
                highest_level = max(highest_level, level)

    return highest_level


async def send_permission_denied(ctx: commands.Context, required_name: str) -> None:
    message = f"❌ You need **{required_name}** rank to use this command."
    try:
        if getattr(ctx, "interaction", None):
            if not ctx.interaction.response.is_done():
                await ctx.interaction.response.send_message(message, ephemeral=True)
            else:
                await ctx.interaction.followup.send(message, ephemeral=True)
        else:
            await ctx.reply(message, delete_after=10)
    except Exception:
        log.exception("Failed to send permission denied message")


async def check_level(ctx: commands.Context, required_level: int) -> bool:
    actual_level = get_level(ctx.author, ctx.bot)
    allowed = actual_level >= required_level

    log.info(
        "[PERM] %s (%s) | cmd=%s | req=%s | act=%s | allowed=%s | loc=%s/%s",
        ctx.author, ctx.author.id,
        get_command_name(ctx),
        get_level_name(required_level),
        get_level_name(actual_level),
        allowed,
        *get_location_names(ctx),
    )

    if allowed:
        return True

    await send_permission_denied(ctx, get_level_name(required_level))
    return False
