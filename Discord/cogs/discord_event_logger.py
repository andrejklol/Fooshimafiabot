from __future__ import annotations
import logging
import uuid
from datetime import datetime, timezone

import discord
from discord.ext import commands
from core.cache import app_state

log = logging.getLogger("discord_event_logger")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _user_name(user):
    return str(user) if user else None


def _user_id(user):
    return str(user.id) if user and getattr(user, "id", None) else None


async def _push_log(payload: dict) -> None:
    """Forward to the dashboard sync engine with a unique external_id."""
    payload.setdefault("external_id", f"disc-{uuid.uuid4().hex[:8]}")
    sync_engine = getattr(app_state, "dashboard_sync", None)
    if not sync_engine:
        return
    try:
        await sync_engine.push_log(**payload)
    except Exception as e:  # noqa: BLE001
        log.error(f"[discord_event_logger] push failed: {e}")


class DiscordEventLogger(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _scan_audit(self, guild, action, target_id):
        """Return (actor_user, reason) for a recent audit-log entry, or
        (None, None) if nothing matches in the last 20s."""
        try:
            async for entry in guild.audit_logs(limit=5, action=action):
                if entry.target and entry.target.id == target_id:
                    age = (datetime.now(timezone.utc) - entry.created_at).total_seconds()
                    if age < 20:
                        return entry.user, entry.reason
        except Exception:  # noqa: BLE001
            pass
        return None, None

    # ─── Moderation (mod-driven) ─────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_ban(self, guild, user):
        staff, reason = await self._scan_audit(
            guild, discord.AuditLogAction.ban, user.id
        )
        # If audit-log scan misses, we don't know the mod — record
        # "Unknown" (a frontend-recognized placeholder that falls
        # through SafeName) instead of "System".
        actor = staff or None
        await _push_log({
            "category": "moderation",
            "action_type": "discord.ban",
            "staff_name": _user_name(actor) or "Unknown",
            "staff_discord_id": _user_id(actor),
            "actor_name": _user_name(actor),
            "actor_discord_id": _user_id(actor),
            "target_name": _user_name(user),
            "target_discord_id": _user_id(user),
            "details": f"Reason: {reason}" if reason else None,
            "reason": reason or "",
            "timestamp": _now_iso(),
        })

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        staff, reason = await self._scan_audit(
            member.guild, discord.AuditLogAction.kick, member.id
        )
        if staff:
            # Mod-driven kick.
            await _push_log({
                "category": "moderation",
                "action_type": "discord.kick",
                "staff_name": _user_name(staff),
                "staff_discord_id": _user_id(staff),
                "actor_name": _user_name(staff),
                "actor_discord_id": _user_id(staff),
                "target_name": _user_name(member),
                "target_discord_id": _user_id(member),
                "details": f"Reason: {reason}" if reason else None,
                "reason": reason or "",
                "timestamp": _now_iso(),
            })
        else:
            # Voluntary leave — the member is the actor.
            await _push_log({
                "category": "info",
                "action_type": "discord.leave",
                "staff_name": _user_name(member),
                "staff_discord_id": _user_id(member),
                "actor_name": _user_name(member),
                "actor_discord_id": _user_id(member),
                "target_name": _user_name(member),
                "target_discord_id": _user_id(member),
                "details": "User left.",
                "timestamp": _now_iso(),
            })

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.roles == after.roles:
            return
        added = [r.name for r in after.roles if r not in before.roles]
        removed = [r.name for r in before.roles if r not in after.roles]

        # Role changes are mod-driven. Scan the audit log for the actor.
        staff, _ = await self._scan_audit(
            after.guild, discord.AuditLogAction.member_role_update, after.id
        )
        # Fallback: the target member themselves (still better than
        # "System" — if a role change happened the member at least
        # got notified about it).
        actor = staff or after

        await _push_log({
            "category": "info",
            "action_type": "discord.member.roles",
            "staff_name": _user_name(actor),
            "staff_discord_id": _user_id(actor),
            "actor_name": _user_name(actor),
            "actor_discord_id": _user_id(actor),
            "target_name": _user_name(after),
            "target_discord_id": _user_id(after),
            "details": f"Added: {added} | Removed: {removed}",
            "timestamp": _now_iso(),
        })

    # ─── Message events ──────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message_delete(self, msg):
        if msg.author.bot or not msg.guild:
            return
        # Try to identify the mod via audit log; otherwise treat this
        # as a self-delete by the author. NEVER label this "System" —
        # that confuses the audit feed.
        staff, _ = await self._scan_audit(
            msg.guild, discord.AuditLogAction.message_delete, msg.author.id
        )
        actor = staff or msg.author
        await _push_log({
            "category": "info",
            "action_type": "discord.message.delete",
            "staff_name": _user_name(actor),
            "staff_discord_id": _user_id(actor),
            "actor_name": _user_name(actor),
            "actor_discord_id": _user_id(actor),
            "target_name": _user_name(msg.author),
            "target_discord_id": _user_id(msg.author),
            "details": f"#{msg.channel.name}: {msg.content[:200]}",
            "timestamp": _now_iso(),
        })

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if before.author.bot or before.content == after.content:
            return
        # Discord only lets the author edit their own messages. The
        # editor is unambiguously the author.
        author = after.author
        await _push_log({
            "category": "info",
            "action_type": "discord.message.edit",
            "staff_name": _user_name(author),
            "staff_discord_id": _user_id(author),
            "actor_name": _user_name(author),
            "actor_discord_id": _user_id(author),
            "target_name": _user_name(author),
            "target_discord_id": _user_id(author),
            "details": f"Old: {before.content[:100]} | New: {after.content[:100]}",
            "timestamp": _now_iso(),
        })

    # ─── Voice ───────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if before.channel == after.channel:
            return
        action = "join" if not before.channel else ("leave" if not after.channel else "move")
        chan = after.channel or before.channel
        # Voice transitions are always self-initiated (server-moves are
        # rare and would surface separately via audit log; ignored here
        # for simplicity).
        await _push_log({
            "category": "info",
            "action_type": f"discord.voice.{action}",
            "staff_name": _user_name(member),
            "staff_discord_id": _user_id(member),
            "actor_name": _user_name(member),
            "actor_discord_id": _user_id(member),
            "target_name": _user_name(member),
            "target_discord_id": _user_id(member),
            "details": f"Channel: {chan.name}",
            "timestamp": _now_iso(),
        })


async def setup(bot: commands.Bot):
    await bot.add_cog(DiscordEventLogger(bot))