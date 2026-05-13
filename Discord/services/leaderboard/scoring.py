from typing import Any

from core.config import (
    BAN_SCORE,
    INVITE_ACCEPT_BONUS,
    KICK_SCORE,
    WARN_SCORE,
)

SCORE_MAPPING: dict[str, int] = {
    "warn": WARN_SCORE,
    "kick": KICK_SCORE,
    "ban": BAN_SCORE,
    "invite": 0,
    "invite_accept": INVITE_ACCEPT_BONUS,
}


def get_action_score(action_type: str) -> int:
    return SCORE_MAPPING.get(action_type, 0)


def build_score_footer() -> str:
    return (
        f"Warn = {WARN_SCORE} • "
        f"Kick = {KICK_SCORE} • "
        f"Ban = {BAN_SCORE} • "
        f"Invite Accepted = {INVITE_ACCEPT_BONUS}"
    )
