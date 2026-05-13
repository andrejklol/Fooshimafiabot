"""SystemModule — handles admin ops initiated from the dashboard.

Currently a single responsibility: when an Owner clicks "Reconcile now"
on the Staff dashboard, the backend emits `admin.reconcile_requested`.
This module consumes it, invokes `registry.reconcile_all()` (which
fans out to every registered module's `reconcile()` method), and
echoes the result back to the dashboard via
`admin.reconcile_completed`.

Scales cleanly: new Owner-triggered bot-side routines are new event
types here, no framework changes.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, ClassVar

from core.base_module import BaseModule

if TYPE_CHECKING:
    from ..core.event_registry import UnifiedEventRegistry

log = logging.getLogger("bot_v2.system")


class SystemModule(BaseModule):
    name = "system"
    handled_events: ClassVar[set[str]] = {
        "admin.reconcile_requested",
    }

    def __init__(self, *, bot: Any = None):
        super().__init__(bot=bot)
        self._registry: "UnifiedEventRegistry | None" = None

    def wire(self, registry: "UnifiedEventRegistry") -> None:
        super().wire(registry)
        # SystemModule uniquely needs a back-reference to the registry
        # so it can call `reconcile_all()`.
        self._registry = registry

    async def on_outbound(self, event_type: str, payload: dict) -> None:
        if event_type != "admin.reconcile_requested":
            return
        if self._registry is None:
            log.error("SystemModule: registry back-reference missing")
            return
        requested_by = (payload or {}).get("requested_by")
        log.info("admin.reconcile_requested by=%s — running reconcile_all", requested_by)
        started = time.monotonic()
        try:
            results = await self._registry.reconcile_all()
        except Exception:
            log.exception("reconcile_all raised — emitting failure echo")
            await self.emit_inbound(
                "admin.reconcile_completed",
                {
                    "requested_by": requested_by,
                    "ok": False,
                    "results": [],
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
            raise
        duration_ms = int((time.monotonic() - started) * 1000)
        log.info(
            "reconcile_all completed in %dms — emitting echo", duration_ms,
        )
        await self.emit_inbound(
            "admin.reconcile_completed",
            {
                "requested_by": requested_by,
                "ok": True,
                "results": results,
                "duration_ms": duration_ms,
            },
        )
