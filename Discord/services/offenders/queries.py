from __future__ import annotations

from typing import Any
from ..leaderboard.processors import resolve_username
from .storage import repeat_offenders


def get_repeat_offenders() -> dict[str, Any]:
    return repeat_offenders


def format_repeat_offenders() -> list[str]:
    if not repeat_offenders:
        return ["No repeat offenders recorded."]

    lines: list[str] = []

    # Sort major threat profiles to the very top dynamically
    sorted_items = sorted(
        repeat_offenders.items(),
        key=lambda x: (
            int(x[1].get("ban", 0) or 0) * 10 +
            int(x[1].get("kick", 0) or 0) * 3 +
            int(x[1].get("warn", 0) or 0)
        ),
        reverse=True
    )

    for user_id, user in sorted_items:
        if not isinstance(user, dict):
            lines.append(f"`{user_id}` | Invalid backend structural tracking profile")
            continue

        # Clean tracking metrics extraction
        name = resolve_username(user_id, user.get("name"))
        warns = int(user.get("warn", 0) or 0)
        kicks = int(user.get("kick", 0) or 0)
        bans = int(user.get("ban", 0) or 0)

        lines.append(
            f"**{name}** (`{user_id[:8]}`) — Warns: `{warns}` | Kicks: `{kicks}` | Bans: `{bans}`"
        )

    return lines
