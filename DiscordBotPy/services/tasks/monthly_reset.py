from datetime import datetime, UTC
import logging

from services.leaderboard.service import reset_monthly_leaderboard_data


log = logging.getLogger("monthly_reset")

last_reset_month: int | None = None


def initialize_monthly_reset_state() -> None:
    """
    Called once when the bot starts so we don't trigger
    a false reset on first loop tick.
    """
    global last_reset_month

    last_reset_month = datetime.now(UTC).month

    log.info(
        "monthly reset initialized month=%s",
        last_reset_month,
    )


async def check_monthly_reset() -> bool:
    """
    Checks once per minute if the month changed.

    Returns:
        True if reset happened
        False if nothing changed
    """
    global last_reset_month

    now = datetime.now(UTC)

    if last_reset_month is None:

        last_reset_month = now.month

        log.info(
            "monthly reset state auto-initialized month=%s",
            last_reset_month,
        )

        return False


    if last_reset_month != now.month:

        log.info(
            "monthly leaderboard reset triggered old=%s new=%s",
            last_reset_month,
            now.month,
        )

        reset_monthly_leaderboard_data()

        last_reset_month = now.month

        return True


    return False