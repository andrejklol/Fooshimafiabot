"""BaseModule — abstract base class every bot feature module inherits.

Contract:
  • `name`            — unique slug, e.g. "moderation", "tickets".
  • `handled_events`  — set of outbound event types this module consumes.
                        Registered with the UnifiedEventRegistry so the
                        dispatcher knows where to route an incoming event.
  • `on_outbound(event_type, payload)` — executes the dashboard-originated
                        change in Discord/VRChat. MUST be idempotent.
  • `on_inbound(event_type, payload)` — called by the module itself (from
                        a slash command or VRChat audit hook) to push a
                        bot-originated change UP to the dashboard. Emits
                        via `InboundEventClient`.
  • `reconcile(bot, sync)` — cold-start state-repair pass. Compare
                        Discord/VRChat reality against the dashboard and
                        apply corrections. MUST be idempotent.

Subclasses never instantiate the HTTP client themselves — `registry.wire()`
injects `self.inbound` and `self.sync` at module-register time.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from .event_registry import UnifiedEventRegistry
    from .inbound_client import InboundEventClient

log = logging.getLogger("bot_v2.base_module")


@dataclass
class ModuleEvent:
    """Shared event shape used for BOTH inbound (bot → dashboard) and
    outbound (dashboard → bot) flows. Keeping one envelope avoids
    bug-inviting asymmetry between the two directions.
    """
    event_type: str
    payload: dict
    # Optional idempotency key. Outbound events from the dashboard
    # already carry one; inbound calls auto-derive if omitted.
    idempotency_key: str | None = None
    actor: str | None = None
    metadata: dict = field(default_factory=dict)


class BaseModule(ABC):
    """Every bot feature (Moderation, Tickets, VRChatBridge, Profiles)
    subclasses this.

    The registry wires `self.inbound` + `self.sync` BEFORE any
    outbound handler fires, so subclasses can assume they're
    populated in every method except `__init__`.
    """

    # Subclass MUST set.
    name: ClassVar[str] = ""
    # Outbound event types this module will consume from the dashboard.
    # Subclass sets as a set literal at class body.
    handled_events: ClassVar[set[str]] = set()

    def __init__(self, bot: Any = None):
        self.bot = bot
        self.inbound: "InboundEventClient | None" = None
        self.sync: Any = None  # DashboardSync instance, opaque here
        self._wired: bool = False

    # Called once by UnifiedEventRegistry.register_module()
    def wire(self, registry: "UnifiedEventRegistry") -> None:
        self.inbound = registry.inbound_client
        self.sync = registry.sync
        self._wired = True
        log.info(
            "module %r wired (handled=%s)",
            self.name, sorted(self.handled_events),
        )

    # ── Outbound (dashboard → bot) ──────────────────────────────
    @abstractmethod
    async def on_outbound(self, event_type: str, payload: dict) -> None:
        """Execute the dashboard-originated change in Discord / VRChat.

        Raising is the signal "do NOT ack this event — redeliver on
        next poll". Returning normally = success, event gets ack'd.
        """
        raise NotImplementedError

    # ── Inbound (bot → dashboard) ───────────────────────────────
    async def emit_inbound(
        self,
        event_type: str,
        payload: dict,
        *,
        idempotency_key: str | None = None,
        actor: str | None = None,
    ) -> dict | None:
        """Post a bot-originated event to /api/events/inbound.

        Thin convenience wrapper around `InboundEventClient.emit()`;
        subclasses call this from slash-command handlers.
        """
        if not self.inbound:
            log.error(
                "module %r tried to emit_inbound before being wired",
                self.name,
            )
            return None
        return await self.inbound.emit(
            event_type, payload,
            idempotency_key=idempotency_key,
            actor=actor,
        )

    # ── Reconciliation (cold-start catch-up) ────────────────────
    async def reconcile(self) -> dict:
        """Compare Discord/VRChat state against the dashboard's source
        of truth and apply corrections. Default is a no-op; override
        per-module. Return value is logged for operator visibility.

        MUST be idempotent — called on every bot restart and may be
        triggered manually via the Owner Debug page.
        """
        return {"module": self.name, "corrected": 0, "skipped": 0, "errors": 0}
