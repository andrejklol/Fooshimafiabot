import discord


def base_embed(title: str = None, description: str = None, color: discord.Color = discord.Color.blue()):
    return discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=discord.utils.utcnow(),
    )


def info_embed(title: str, description: str = ""):
    return base_embed(title=f"ℹ️ {title}", description=description, color=discord.Color.blue())

def success_embed(title: str, description: str = ""):
    return base_embed(title=f"✅ {title}", description=description, color=discord.Color.green())

def warning_embed(title: str, description: str = ""):
    return base_embed(title=f"⚠️ {title}", description=description, color=discord.Color.gold())

def error_embed(title: str, description: str = ""):
    return base_embed(title=f"❌ {title}", description=description, color=discord.Color.red())

def owner_embed(title: str, description: str = ""):
    return base_embed(title=f"👑 {title}", description=description, color=discord.Color.purple())


def ban_action_embed(target: str, reason: str, actor: str, duration: str = "Permanent"):
    embed = base_embed(title="🔨 User Banned", color=discord.Color.red())
    embed.add_field(name="Target", value=target, inline=True)
    embed.add_field(name="Moderator", value=actor, inline=True)
    embed.add_field(name="Duration", value=duration, inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    return embed

def kick_action_embed(target: str, reason: str, actor: str):
    embed = base_embed(title="👢 User Kicked", color=discord.Color.orange())
    embed.add_field(name="Target", value=target, inline=True)
    embed.add_field(name="Moderator", value=actor, inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    return embed

def warn_action_embed(target: str, reason: str, actor: str):
    embed = base_embed(title="⚠️ User Warned", color=discord.Color.gold())
    embed.add_field(name="Target", value=target, inline=True)
    embed.add_field(name="Moderator", value=actor, inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    return embed
