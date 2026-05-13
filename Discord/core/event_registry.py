from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .base_module import BaseModule
    from .inbound_client import InboundEventClient

log = logging.getLogger("bot_v2.registry")


class UnifiedEventRegistry:
    """
    One registry per bot process.
    Central idempotent dispatch layer.
    """

    _SEEN_MAX = 10_000

    def __init__(self, *, sync: Any, inbound_client: "InboundEventClient"):
        self.sync = sync
        self.inbound_client = inbound_client
        self._modules: dict[str, "BaseModule"] = {}
        self._event_map: dict[str, "BaseModule"] = {}

        # event_id → None (LRU)
        self._seen_event_ids: OrderedDict[str, None] = OrderedDict()
        self._lock = asyncio.Lock()

    # ─────────────────────────────────────────────
    # NORMALIZATION (NEW SAFETY LAYER)
    # ─────────────────────────────────────────────
    def _resolve_event_id(self, event: dict) -> str | None:
        """
        Ensures every event has a stable dedupe key.
        """
        eid = (
            event.get("event_id")
            or event.get("id")
            or event.get("external_id")
        )

        if eid is None:
            return None

        return str(eid)

    # ── Registration ────────────────────────────────────────────

    def register_module(self, module: "BaseModule") -> None:
        if not module.name:
            raise ValueError(f"{type(module).__name__} must set class-level `name`")

        if module.name in self._modules:
            raise ValueError(f"Module {module.name!r} already registered")

        module.wire(self)
        self._modules[module.name] = module

        for et in module.handled_events:
            if et in self._event_map:
                raise ValueError(
                    f"Event {et!r} already owned by {self._event_map[et].name!r}"
                )
            self._event_map[et] = module

        log.info(
            "registered module=%s events=%s",
            module.name,
            sorted(module.handled_events),
        )

    # ── Unified event dispatch ──────────────────────────────────

    async def process_event(self, event: dict) -> bool:
        """
        Central entry point (idempotent).
        """

        event_id = self._resolve_event_id(event)
        event_type = event.get("event_type", "")
        payload = event.get("payload") or {}

        # ─────────────────────────────────────────────
        # IDEMPOTENCY GATE (HARDENED)
        # ─────────────────────────────────────────────
        async with self._lock:
            if event_id:
                if event_id in self._seen_event_ids:
                    log.debug(
                        "duplicate event ignored id=%s type=%s",
                        event_id,
                        event_type,
                    )
                    return True

                self._seen_event_ids[event_id] = None

                # LRU trim
                if len(self._seen_event_ids) > self._SEEN_MAX:
                    self._seen_event_ids.popitem(last=False)

        module = self._event_map.get(event_type)

        if module is None:
            log.debug(
                "no handler for event_type=%r id=%s (acked)",
                event_type,
                event_id,
            )
            return True

        try:
            await module.on_outbound(event_type, payload)
            return True

        except Exception:
            log.exception(
                "handler failed module=%s type=%s id=%s",
                module.name,
                event_type,
                event_id,
            )
            return False

    # ── Bulk ops ────────────────────────────────────────────────

    async def reconcile_all(self) -> list[dict]:
        results = []

        for module in self._modules.values():
            try:
                r = await module.reconcile()
            except Exception as exc:
                log.exception("reconcile failed module=%s", module.name)
                r = {
                    "module": module.name,
                    "errors": 1,
                    "exception": repr(exc),
                }
            results.append(r)

        return results

    # ── Accessors ───────────────────────────────────────────────

    def get(self, name: str) -> "BaseModule | None":
        return self._modules.get(name)

    @property
    def modules(self) -> dict[str, "BaseModule"]:
        return dict(self._modules)

    @property
    def known_event_types(self) -> set[str]:
        return set(self._event_map.keys())
