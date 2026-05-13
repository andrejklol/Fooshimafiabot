"""Bot feature modules (new BaseModule-based architecture).

Each module owns a single bounded context (moderation, tickets,
profiles, vrchat-bridge). Import the ones your bot needs and register
them on the UnifiedEventRegistry from your `on_ready` handler.
"""
from .moderation_module import ModerationModule
from .profiles_module import ProfilesModule
from .system_module import SystemModule
from .vrchat_bridge_module import VRChatBridgeModule

__all__ = ["ModerationModule", "ProfilesModule", "SystemModule", "VRChatBridgeModule"]
