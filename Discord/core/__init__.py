"""Reflection Layer architecture — core primitives.

The bot is a *reflection layer*, not a system of record. Dashboard DB
is the single source of truth. Every action in Discord that mutates
state MUST flow through:

    Discord slash command  ─┐
                            ├─►  BaseModule.on_inbound()  ─►  POST /api/events/inbound
    VRChat audit event     ─┘                                       │
                                                                    ▼
                                                          Dashboard mutation
                                                                    │
                                        ◄─── OUTBOUND event ◄───────┘

The bot receives the outbound event via WebSocket (primary) or 60s
polling (fallback) and runs `on_outbound()` to mirror the change into
Discord / VRChat. This round-trip guarantees that the DB state is what
actually happened, and the bot stays stateless.
"""
from .base_module import BaseModule, ModuleEvent
from .event_registry import UnifiedEventRegistry
from .inbound_client import InboundEventClient
from .reflection_layer import ReflectionLayer

__all__ = [
    "BaseModule",
    "ModuleEvent",
    "UnifiedEventRegistry",
    "InboundEventClient",
    "ReflectionLayer",
]
