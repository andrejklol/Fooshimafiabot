from datetime import datetime, UTC
import logging

from services.leaderboard.service import reset_monthly_leaderboard_data


log = logging.getLogger("monthly_reset")

_last_reset_month: int | None = None


def _current_month() -> int:
    return datetime.now(UTC).month


def initialize_monthly_reset_state() -> None:
    """
    Called once at bot startup so the first loop tick
    does not trigger a false reset.
    """
    global _last_reset_month

    _last_reset_month = _current_month()

    log.info(
        "monthly reset initialized month=%s",
        _last_reset_month,
    )


async def check_monthly_reset() -> bool:
    """
    Checks if the month changed.

    Returns:
        True  -> reset triggered
        False -> no reset needed
    """
    global _last_reset_month

    current_month = _current_month()

    # safety init if initialize wasn't called
    if _last_reset_month is None:
        _last_reset_month = current_month

        log.info(
            "monthly reset state auto-initialized month=%s",
            _last_reset_month,
        )

        return False

    # month changed -> reset leaderboard
    if _last_reset_month != current_month:
        log.info(
            "monthly leaderboard reset triggered old=%s new=%s",
            _last_reset_month,
            current_month,
        )

        reset_monthly_leaderboard_data()

        _last_reset_month = current_month

        return True

    return False
