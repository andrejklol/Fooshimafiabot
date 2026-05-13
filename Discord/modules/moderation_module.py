from __future__ import annotations

import logging
from typing import Any, ClassVar, Optional

from core.base_module import BaseModule
from .vrchat_bridge_module import VRChatBridgeModule

log = logging.getLogger("bot_v2.moderation")

MODERATION_EVENT_TYPES: frozenset[str] = frozenset({
    "moderation.warn",
    "moderation.kick",
    "moderation.ban",
})

class ModerationModule(BaseModule):
    name = "moderation"
    handled_events: ClassVar[set[str]] = set(MODERATION_EVENT_TYPES)

    def __init__(
        self,
        *,
        bot: Any = None,
        guild_id_getter,
        vrchat_bridge: Optional[VRChatBridgeModule] = None,
    ):
        super().__init__(bot=bot)
        self._guild_id = guild_id_getter
        self._bridge = vrchat_bridge

    # ── Inbound (Discord slash command → dashboard) ────────────

    async def report_warn(
        self,
        *,
        actor_discord_id: str,
        actor_discord_username: str,
        target_discord_id: Optional[str] = None,
        target_discord_username: Optional[str] = None,
        target_vrchat_user_id: Optional[str] = None,
        reason: str = "",
        external_id: Optional[str] = None,
    ) -> dict | None:
        return await self._emit_moderation_action(
            "moderation.warn",
            actor_discord_id=actor_discord_id,
            actor_discord_username=actor_discord_username,
            target_discord_id=target_discord_id,
            target_discord_username=target_discord_username,
            target_vrchat_user_id=target_vrchat_user_id,
            reason=reason,
            external_id=external_id,
        )

    async def report_kick(self, **kwargs) -> dict | None:
        return await self._emit_moderation_action("moderation.kick", **kwargs)

    async def report_ban(self, **kwargs) -> dict | None:
        return await self._emit_moderation_action("moderation.ban", **kwargs)

    async def _emit_moderation_action(
        self,
        event_type: str,
        *,
        actor_discord_id: str,
        actor_discord_username: str,
        target_discord_id: Optional[str] = None,
        target_discord_username: Optional[str] = None,
        target_vrchat_user_id: Optional[str] = None,
        reason: str = "",
        external_id: Optional[str] = None,
    ) -> dict | None:
        payload = {
            "actor_discord_id": str(actor_discord_id),
            "actor_discord_username": actor_discord_username,
            "target_discord_id": (
                str(target_discord_id) if target_discord_id else None
            ),
            "target_discord_username": target_discord_username,
            "target_vrchat_user_id": target_vrchat_user_id,
            "reason": reason,
            "external_id": external_id,
        }
        return await self.emit_inbound(
            event_type, payload, actor=actor_discord_username,
        )

    # ── Outbound (dashboard → Discord/VRChat) ──────────────────

    async def on_outbound(self, event_type: str, payload: dict) -> None:
        if event_type not in MODERATION_EVENT_TYPES:
            log.debug("moderation: ignoring unrelated event_type=%s", event_type)
            return

        guild = self._resolve_guild()
        target_discord_id = payload.get("target_discord_id")
        target_vrchat_user_id = payload.get("target_vrchat_user_id")
        reason = (payload.get("reason") or "Moderation via dashboard")[:200]

        if guild and target_discord_id:
            await self._apply_discord_side(
                guild, event_type, target_discord_id, reason,
            )
        else:
            log.debug(
                "moderation: Discord side skipped (guild=%s target_discord_id=%s)",
                bool(guild), target_discord_id,
            )

        if (
            self._bridge is not None
            and target_vrchat_user_id
            and event_type in {"moderation.kick", "moderation.ban"}
        ):
            if event_type == "moderation.kick":
                await self._bridge.kick_user(
                    target_vrchat_user_id, reason=reason,
                )
            else:
                await self._bridge.ban_user(
                    target_vrchat_user_id, reason=reason,
                )

    async def _apply_discord_side(
        self, guild: Any, event_type: str, target_id: str, reason: str,
    ) -> None:
        try:
            target_id_int = int(target_id)
        except (TypeError, ValueError):
            log.warning("moderation: non-numeric target_discord_id=%r", target_id)
            return

        member = guild.get_member(target_id_int)
        try:
            if event_type == "moderation.warn":
                if member:
                    try:
                        await member.send(
                            f":warning: You've been warned in **{guild.name}**. "
                            f"Reason: {reason}",
                        )
                    except Exception:
                        log.info("warn DM failed (member DMs closed) — continuing")
            elif event_type == "moderation.kick":
                if member:
                    await member.kick(reason=f"Dashboard: {reason}")
                else:
                    log.info("moderation.kick: member %s not in guild", target_id)
            elif event_type == "moderation.ban":
                user = member or await self.bot.fetch_user(target_id_int)
                await guild.ban(user, reason=f"Dashboard: {reason}")
        except Exception:
            log.exception(
                "moderation Discord side failed type=%s target=%s",
                event_type, target_id,
            )
            raise

    def _resolve_guild(self) -> Any:
        if not self.bot:
            return None
        try:
            return self.bot.get_guild(int(self._guild_id()))
        except (TypeError, ValueError):
            log.error("moderation: guild_id_getter returned non-int")
            return None

    # ── Reconcile (cold-start state repair) ────────────────────

    async def reconcile(self) -> dict:
        corrected = 0
        skipped = 0
        errors = 0
        try:
            dashboard_banned: set[int] = await self._fetch_dashboard_banned_ids()
        except Exception:
            log.exception("reconcile: could not fetch dashboard banned ids")
            return {
                "module": self.name,
                "corrected": 0, "skipped": 0, "errors": 1,
            }

        guild = self._resolve_guild()
        if not guild:
            return {
                "module": self.name,
                "corrected": 0, "skipped": 0, "errors": 0,
                "note": "guild unavailable — reconcile deferred",
            }

        try:
            discord_banned: set[int] = set()
            async for entry in guild.bans():
                discord_banned.add(entry.user.id)
        except Exception as exc:
            # --- PATCH APPLIED HERE ---
            msg = str(exc).lower()
            if "403" in msg or "forbidden" in msg or "missing permissions" in msg:
                log.warning(
                    "moderation.reconcile: bot lacks Ban Members permission in "
                    "guild %s — skipping ban reconciliation (grant the role "
                    "`Ban Members` in Discord to enable).",
                    getattr(guild, "id", "?"),
                )
                return {
                    "module": self.name,
                    "corrected": 0, "skipped": 1, "errors": 0,
                    "note": "skipped: missing Ban Members permission",
                }
            log.exception("reconcile: fetching Discord ban list failed")
            errors += 1
            return {
                "module": self.name,
                "corrected": corrected, "skipped": skipped, "errors": errors,
            }

        # Apply missing bans (dashboard → Discord).
        for uid in dashboard_banned - discord_banned:
            try:
                user = await self.bot.fetch_user(uid)
                await guild.ban(user, reason="Reconcile: banned on dashboard")
                corrected += 1
            except Exception:
                log.exception("reconcile: could not ban uid=%s", uid)
                errors += 1
                
        # Apply missing unbans (dashboard → Discord).
        for uid in discord_banned - dashboard_banned:
            try:
                user = await self.bot.fetch_user(uid)
                await guild.unban(user, reason="Reconcile: cleared on dashboard")
                corrected += 1
            except Exception:
                log.exception("reconcile: could not unban uid=%s", uid)
                errors += 1
                
        if not (dashboard_banned ^ discord_banned):
            skipped = 1
            
        return {
            "module": self.name,
            "corrected": corrected, "skipped": skipped, "errors": errors,
            "dashboard_banned": len(dashboard_banned),
            "discord_banned": len(discord_banned),
        }

    async def _fetch_dashboard_banned_ids(self) -> set[int]:
        session = await self.sync._get_session()
        url = f"{self.sync.base_url}/offenders"
        ids: set[int] = set()
        page = 1
        while True:
            async with session.get(
                url,
                params={"severity": "ban", "page": page, "page_size": 100},
            ) as resp:
                if resp.status != 200:
                    break
                data = await resp.json()
            items = data.get("items") if isinstance(data, dict) else data
            if not items:
                break
            for row in items:
                did = row.get("discord_id")
                if isinstance(did, str) and did.isdigit():
                    ids.add(int(did))
            if len(items) < 100:
                break
            page += 1
        return ids
