"""VRChatBridgeModule — proxies Group moderation API calls.

The dashboard cannot call the VRChat API directly (VRChat uses a 2FA
session cookie bound to the bot's logged-in account). So every
VRChat-side action the dashboard wants to run — kick, ban — gets
routed through this module.

Surface is deliberately narrow for this first cut: `kick_user`,
`ban_user`. Extend with `unban_user`, `post_group_announcement`, etc.
by adding more methods + registering the event types.

Dependency injection: the module needs the bot's `app_state.vrc_groups_api`
(a `vrchatapi.GroupsApi` instance) and `app_state.vrc_group_id`
(the group id string from config). Supply via the constructor.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional

from core.base_module import BaseModule

log = logging.getLogger("bot_v2.vrchat_bridge")


class VRChatBridgeModule(BaseModule):
    name = "vrchat_bridge"
    # Directly-handled outbound events (dashboard → bot → VRChat).
    # Moderation module also calls into this bridge for the VRChat
    # side of its warn/kick/ban, so those event types are NOT listed
    # here — owned by ModerationModule.
    handled_events = {
        "vrchat.group_kick",
        "vrchat.group_ban",
    }

    def __init__(
        self,
        *,
        bot: Any = None,
        vrc_groups_api_getter: Callable[[], Any],
        group_id_getter: Callable[[], str],
        run_blocking: Optional[Callable] = None,
    ):
        """`vrc_groups_api_getter` + `group_id_getter` are callables
        (not plain values) so a reconnecting VRChat client instance
        doesn't leave this module holding a stale reference. They're
        invoked per call.

        `run_blocking` is the bot's "run sync func in executor"
        helper; when omitted, we default to `asyncio.to_thread`.
        """
        super().__init__(bot=bot)
        self._vrc_groups_api = vrc_groups_api_getter
        self._group_id = group_id_getter
        self._run_blocking = run_blocking or asyncio.to_thread

    # ── Outbound handler ────────────────────────────────────────

    async def on_outbound(self, event_type: str, payload: dict) -> None:
        user_id = str(payload.get("vrchat_user_id") or "").strip()
        if not user_id:
            log.warning(
                "vrchat_bridge: payload missing vrchat_user_id type=%s payload=%s",
                event_type, payload,
            )
            return
        if event_type == "vrchat.group_kick":
            await self.kick_user(user_id, reason=payload.get("reason"))
        elif event_type == "vrchat.group_ban":
            await self.ban_user(user_id, reason=payload.get("reason"))
        else:
            log.debug("vrchat_bridge: unknown event_type=%s", event_type)

    # ── Public helpers (also called by ModerationModule) ───────

    async def kick_user(self, vrchat_user_id: str, *, reason: Optional[str] = None) -> bool:
        api = self._vrc_groups_api()
        group_id = self._group_id()
        if not api or not group_id:
            log.error("vrchat_bridge.kick_user: groups_api or group_id not ready")
            return False
        log.info(
            "vrchat_bridge: kick group=%s user=%s reason=%s",
            group_id, vrchat_user_id, reason,
        )
        try:
            await self._run_blocking(
                api.kick_group_member, group_id, vrchat_user_id,
            )
            # Inbound echo so the dashboard knows the VRChat-side
            # action actually completed (vs. dashboard-side "intent").
            await self.emit_inbound(
                "vrchat.group_kick.confirmed",
                {
                    "vrchat_user_id": vrchat_user_id,
                    "reason": reason,
                    "group_id": group_id,
                },
            )
            return True
        except Exception as exc:
            log.exception(
                "vrchat_bridge.kick_user failed user=%s: %r",
                vrchat_user_id, exc,
            )
            await self.emit_inbound(
                "vrchat.group_kick.failed",
                {
                    "vrchat_user_id": vrchat_user_id,
                    "reason": reason,
                    "error": str(exc)[:200],
                },
            )
            # Re-raise so the registry leaves the event un-ack'd and
            # the dashboard retries on next poll (transient failure).
            raise

    async def ban_user(self, vrchat_user_id: str, *, reason: Optional[str] = None) -> bool:
        api = self._vrc_groups_api()
        group_id = self._group_id()
        if not api or not group_id:
            log.error("vrchat_bridge.ban_user: groups_api or group_id not ready")
            return False
        log.info(
            "vrchat_bridge: ban group=%s user=%s reason=%s",
            group_id, vrchat_user_id, reason,
        )
        try:
            await self._run_blocking(
                api.ban_group_member, group_id, vrchat_user_id,
            )
            await self.emit_inbound(
                "vrchat.group_ban.confirmed",
                {
                    "vrchat_user_id": vrchat_user_id,
                    "reason": reason,
                    "group_id": group_id,
                },
            )
            return True
        except Exception as exc:
            log.exception(
                "vrchat_bridge.ban_user failed user=%s: %r",
                vrchat_user_id, exc,
            )
            await self.emit_inbound(
                "vrchat.group_ban.failed",
                {
                    "vrchat_user_id": vrchat_user_id,
                    "reason": reason,
                    "error": str(exc)[:200],
                },
            )
            raise
