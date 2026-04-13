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
# matches partial role names
# ============================================================

ROLE_LEVEL_MAP = {
    # owner
    "godfooshi": LEVEL_OWNER,

    # underboss
    "underboss": LEVEL_UNDERBOSS,

    # consigliere
    "consigliere": LEVEL_CONSIGLIERE,

    # capo
    "capo": LEVEL_CAPO,

    # soldier
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

    for user_role in roles:
        for role_name, level in ROLE_LEVEL_MAP.items():
            if role_name in user_role:
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

    guild_name = ctx.guild.name if ctx.guild else "DM"
    channel_name = getattr(ctx.channel, "name", "DM") if ctx.guild else "DM"

    log.info(
        "[PERM] user=%s (%s) | command=%s | required=%s | actual=%s | allowed=%s | guild=%s | channel=%s",
        ctx.author,
        ctx.author.id,
        ctx.command.name if ctx.command else "unknown",
        required_name,
        actual_name,
        allowed,
        guild_name,
        channel_name,
    )

    if not allowed:
        try:
            if getattr(ctx, "interaction", None):
                if not ctx.interaction.response.is_done():
                    await ctx.interaction.response.send_message(
                        f"❌ You need **{required_name}** rank to use this command.",
                        ephemeral=True,
                    )
                else:
                    await ctx.interaction.followup.send(
                        f"❌ You need **{required_name}** rank to use this command.",
                        ephemeral=True,
                    )
            else:
                await ctx.reply(
                    f"❌ You need **{required_name}** rank to use this command.",
                    delete_after=10,
                )
        except Exception:
            pass

        return False

    return True
