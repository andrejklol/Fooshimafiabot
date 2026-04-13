import logging

import discord
from discord.ext import commands

from core.embeds import warning_embed
from core.utils import respond

log = logging.getLogger("command_perms")

OWNER_USER_ID = 1470116079459242139

ROLE_IDS = {
    "godfooshi": 1470116079459242139,
    "underboss": 1470116287010046166,
    "consigliere": 1487940866570977301,
    "capo": 1470116372007616683,
    "soldier": 1479536640316805296,
    "associate": 1470116440982949898,
}

ROLE_LEVELS = {
    ROLE_IDS["godfooshi"]: 5,
    ROLE_IDS["underboss"]: 4,
    ROLE_IDS["consigliere"]: 3,
    ROLE_IDS["capo"]: 2,
    ROLE_IDS["soldier"]: 1,
    ROLE_IDS["associate"]: 0,
}

LEVEL_OWNER = 5
LEVEL_UNDERBOSS = 4
LEVEL_CONSIGLIERE = 3
LEVEL_CAPO = 2
LEVEL_SOLDIER = 1
LEVEL_ASSOCIATE = 0

_PERMISSION_DENIED_EMBED = warning_embed(
    "Permission Denied",
    "You do not have permission to use this command.",
)


def get_permission_level(member: discord.Member | discord.User) -> int:
    if member.id == OWNER_USER_ID:
        return 999

    roles = getattr(member, "roles", None)
    if not roles:
        return 0

    return max((ROLE_LEVELS.get(role.id, 0) for role in roles), default=0)


async def check_command_level(ctx: commands.Context, required_level: int) -> bool:
    user_level = get_permission_level(ctx.author)
    command_name = getattr(ctx.command, "qualified_name", "unknown")

    log.info(
        "[cmd_perm] user=%s user_id=%s command=%s required=%s actual=%s allowed=%s",
        ctx.author,
        ctx.author.id,
        command_name,
        required_level,
        user_level,
        user_level >= required_level,
    )

    if user_level >= required_level:
        return True

    await respond(ctx, embed=_PERMISSION_DENIED_EMBED, ephemeral=True)
    return False
