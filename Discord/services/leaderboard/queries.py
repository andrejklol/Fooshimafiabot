from collections import Counter
from typing import Any

from .storage import leaderboard_data


# -------------------------
# INTERNAL HELPERS
# -------------------------

def _get_scope_data(scope: str = "staff") -> dict[str, Any]:
    data = leaderboard_data.get(scope, {})
    return data if isinstance(data, dict) else {}


def _build_activity_counter(scope: str) -> Counter:
    counter = Counter()
    for staff in _get_scope_data(scope).values():
        total = (
            int(staff.get("warn", 0))
            + int(staff.get("kick", 0))
            + int(staff.get("ban", 0))
            + int(staff.get("invite_accept", 0))
        )
        if total > 0:
            counter[staff.get("name", "Unknown")] = total
    return counter


def _build_stat_counter(scope: str, stat_key: str) -> Counter:
    return Counter({
        staff.get("name", "Unknown"): int(staff.get(stat_key, 0))
        for staff in _get_scope_data(scope).values()
        if int(staff.get(stat_key, 0)) > 0
    })


# -------------------------
# PUBLIC APIS
# -------------------------

def get_top_staff(limit: int = 10, scope: str = "staff") -> list[dict[str, Any]]:
    scope_data = _get_scope_data(scope)
    sorted_staff = sorted(
        scope_data.values(),
        key=lambda x: int(x.get("points", 0)),
        reverse=True,
    )
    return sorted_staff[:limit]


def get_staff_stats(staff_id: str, scope: str = "staff") -> dict[str, Any] | None:
    return _get_scope_data(scope).get(str(staff_id))


def format_leaderboard_lines(limit: int = 10, scope: str = "staff") -> list[str]:
    return [
        f"{i}. {staff.get('name', 'Unknown')} — {int(staff.get('points', 0))} pts"
        for i, staff in enumerate(get_top_staff(limit, scope=scope), start=1)
    ]


# -------------------------
# AGGREGATION COUNTERS
# -------------------------

def build_overall_activity_counter() -> Counter:
    return _build_activity_counter("staff")


def build_monthly_activity_counter() -> Counter:
    return _build_activity_counter("monthly")


# --- Overall Stat Metrics ---

def build_overall_warn_counter() -> Counter:
    return _build_stat_counter("staff", "warn")


def build_overall_kick_counter() -> Counter:
    return _build_stat_counter("staff", "kick")


def build_overall_ban_counter() -> Counter:
    return _build_stat_counter("staff", "ban")


def build_overall_invite_counter() -> Counter:
    return _build_stat_counter("staff", "invite_accept")


# --- Monthly Stat Metrics ---

def build_monthly_warn_counter() -> Counter:
    return _build_stat_counter("monthly", "warn")


def build_monthly_kick_counter() -> Counter:
    return _build_stat_counter("monthly", "kick")


def build_monthly_ban_counter() -> Counter:
    return _build_stat_counter("monthly", "ban")


def build_monthly_invite_counter() -> Counter:
    return _build_stat_counter("monthly", "invite_accept")
