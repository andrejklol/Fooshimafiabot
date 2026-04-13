import logging
import discord
from discord.ext import commands

log = logging.getLogger("cmd_perms")


LEVEL_OWNER = 5
LEVEL_UNDERBOSS = 4
LEVEL_CONSIGLIERE = 3
LEVEL_CAPO = 2
LEVEL_SOLDIER = 1
LEVEL_USER = 0


ROLE_LEVEL_MAP = {
    "godfooshi": LEVEL_OWNER,
    "owner": LEVEL_OWNER,
    "underboss": LEVEL_UNDERBOSS,
    "consigliere": LEVEL_CONSIGLIERE,
    "capo": LEVEL_CAPO,
    "soldier": LEVEL_SOLDIER,
}


def get_level(member: discord.Member) -> int:

    roles = [r.name.lower() for r in getattr(member, "roles", [])]

    for role_name, level in ROLE_LEVEL_MAP.items():
        if role_name in roles:
            return level

    return LEVEL_USER


async def check_level(ctx: commands.Context, required_level: int) -> bool:

    actual_level = get_level(ctx.author)

    log.info(
        "[PERM] user=%s command=%s required=%s actual=%s allowed=%s",
        ctx.author,
        ctx.command.name,
        required_level,
        actual_level,
        actual_level >= required_level,
    )

    if actual_level < required_level:
        return False

    return True
