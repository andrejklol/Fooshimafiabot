from core.config import (
    WARN_SCORE,
    KICK_SCORE,
    BAN_SCORE,
    INVITE_ACCEPT_BONUS,
)


def get_action_score(action_type: str) -> int:

    if action_type == "warn":
        return WARN_SCORE

    if action_type == "kick":
        return KICK_SCORE

    if action_type == "ban":
        return BAN_SCORE

    if action_type == "invite":
        return 0

    if action_type == "invite_accept":
        return INVITE_ACCEPT_BONUS

    return 0


def build_score_footer() -> str:

    return (
        f"Warn = {WARN_SCORE} • "
        f"Kick = {KICK_SCORE} • "
        f"Ban = {BAN_SCORE} • "
        f"Invite Accepted = {INVITE_ACCEPT_BONUS}"
    )