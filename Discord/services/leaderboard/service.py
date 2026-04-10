from .history_loader import load_full_history
from .processors import process_audit_log_entry

from .queries import (
    get_top_staff,
    get_staff_stats,
    format_leaderboard_lines,
    build_overall_activity_counter,
    build_monthly_activity_counter,
    build_overall_warn_counter,
    build_monthly_warn_counter,
    build_overall_kick_counter,
    build_monthly_kick_counter,
    build_overall_ban_counter,
    build_monthly_ban_counter,
    build_overall_invite_counter,
    build_monthly_invite_counter,
)

from .scoring import (
    build_score_footer,
    get_action_score,
)

from .storage import (
    leaderboard_data,
    load_leaderboard_data,
    save_leaderboard_data,
    reset_leaderboard_data,
    reset_monthly_leaderboard_data,
)


# ============================================================
# STARTUP SEED
# ============================================================

async def seed_leaderboards(
        amount: int = 1000,
) -> int:
    """
    Initial leaderboard warmup.

    Used on first startup when no leaderboard data exists.
    Loads a chunk of history so commands show results immediately.
    """

    print(f"[leaderboard] seeding leaderboard with {amount} logs")

    loaded = await load_full_history(
        limit=amount,
        rebuild=False,
        monthly_only=False,
    )

    print(f"[leaderboard] seed complete loaded={loaded}")

    return loaded