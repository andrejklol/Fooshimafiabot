import logging
import discord
from discord.ext import commands

log = logging.getLogger("cmd_perms")


# ============================================================
# LEVEL VALUES
# ============================================================

LEVEL_OWNER = 5
LEVEL_UNDERBOSS = 4
LEVEL_CONSIGLIERE = 3
LEVEL_CAPO = 2
LEVEL_SOLDIER = 1
LEVEL_USER = 0


# ============================================================
# ROLE NAME → LEVEL
# ============================================================

ROLE_LEVEL_MAP = {
    "godfooshi": LEVEL_OWNER,
    "owner": LEVEL_OWNER,

    "underboss": LEVEL_UNDERBOSS,

    "consigliere": LEVEL_CONSIGLIERE,

    "capo": LEVEL_CAPO,

    "soldier": LEVEL_SOLDIER,
}


# ============================================================
# LEVEL NAME LOOKUP
# ============================================================

LEVEL_NAMES = {
    LEVEL_OWNER: "OWNER",
    LEVEL_UNDERBOSS: "UNDERBOSS",
    LEVEL_CONSIGLIERE: "CONSIGLIERE",
    LEVEL_CAPO: "CAPO",
    LEVEL_SOLDIER: "SOLDIER",
    LEVEL_USER: "USER",
}


# ============================================================
# GET USER LEVEL
# ============================================================

def get_level(member: discord.Member) -> int:

    roles = [r.name.lower() for r in getattr(member, "roles", [])]

    highest_level = LEVEL_USER

    for role_name, level in ROLE_LEVEL_MAP.items():

        if role_name in roles:
            highest_level = max(highest_level, level)

    return highest_level


# ============================================================
# CHECK LEVEL + LOG
# ============================================================

async def check_level(ctx: commands.Context, required_level: int) -> bool:

    actual_level = get_level(ctx.author)

    required_name = LEVEL_NAMES.get(required_level, str(required_level))
    actual_name = LEVEL_NAMES.get(actual_level, str(actual_level))

    allowed = actual_level >= required_level

    log.info(
        "[PERM] user=%s | command=%s | required=%s | actual=%s | allowed=%s",
        ctx.author,
        ctx.command.name,
        required_name,
        actual_name,
        allowed,
    )

    return allowed
