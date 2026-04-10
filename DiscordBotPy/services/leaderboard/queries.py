from collections import Counter

from .storage import leaderboard_data


def _get_scope_data(scope: str = "staff") -> dict:
    data = leaderboard_data.get(scope, {})
    return data if isinstance(data, dict) else {}


def get_top_staff(limit: int = 10, scope: str = "staff"):
    scope_data = _get_scope_data(scope)

    sorted_staff = sorted(
        scope_data.values(),
        key=lambda x: int(x.get("points", 0)),
        reverse=True,
    )

    return sorted_staff[:limit]


def get_staff_stats(staff_id: str, scope: str = "staff"):
    scope_data = _get_scope_data(scope)
    return scope_data.get(staff_id)


def format_leaderboard_lines(limit: int = 10, scope: str = "staff"):
    lines = []

    for i, staff in enumerate(get_top_staff(limit, scope=scope), start=1):
        lines.append(
            f"{i}. {staff.get('name', 'Unknown')} — {int(staff.get('points', 0))} pts"
        )

    return lines


def build_overall_activity_counter() -> Counter:
    counter = Counter()

    for staff in _get_scope_data("staff").values():
        total = (
                int(staff.get("warn", 0))
                + int(staff.get("kick", 0))
                + int(staff.get("ban", 0))
                + int(staff.get("invite_accept", 0))
        )

        if total > 0:
            counter[staff.get("name", "Unknown")] = total

    return counter


def build_monthly_activity_counter() -> Counter:
    counter = Counter()

    for staff in _get_scope_data("monthly").values():
        total = (
                int(staff.get("warn", 0))
                + int(staff.get("kick", 0))
                + int(staff.get("ban", 0))
                + int(staff.get("invite_accept", 0))
        )

        if total > 0:
            counter[staff.get("name", "Unknown")] = total

    return counter


def build_overall_warn_counter() -> Counter:
    return Counter(
        {
            staff.get("name", "Unknown"): int(staff.get("warn", 0))
            for staff in _get_scope_data("staff").values()
            if int(staff.get("warn", 0)) > 0
        }
    )


def build_overall_kick_counter() -> Counter:
    return Counter(
        {
            staff.get("name", "Unknown"): int(staff.get("kick", 0))
            for staff in _get_scope_data("staff").values()
            if int(staff.get("kick", 0)) > 0
        }
    )


def build_overall_ban_counter() -> Counter:
    return Counter(
        {
            staff.get("name", "Unknown"): int(staff.get("ban", 0))
            for staff in _get_scope_data("staff").values()
            if int(staff.get("ban", 0)) > 0
        }
    )


def build_overall_invite_counter() -> Counter:
    return Counter(
        {
            staff.get("name", "Unknown"): int(staff.get("invite_accept", 0))
            for staff in _get_scope_data("staff").values()
            if int(staff.get("invite_accept", 0)) > 0
        }
    )


def build_monthly_warn_counter() -> Counter:
    return Counter(
        {
            staff.get("name", "Unknown"): int(staff.get("warn", 0))
            for staff in _get_scope_data("monthly").values()
            if int(staff.get("warn", 0)) > 0
        }
    )


def build_monthly_kick_counter() -> Counter:
    return Counter(
        {
            staff.get("name", "Unknown"): int(staff.get("kick", 0))
            for staff in _get_scope_data("monthly").values()
            if int(staff.get("kick", 0)) > 0
        }
    )


def build_monthly_ban_counter() -> Counter:
    return Counter(
        {
            staff.get("name", "Unknown"): int(staff.get("ban", 0))
            for staff in _get_scope_data("monthly").values()
            if int(staff.get("ban", 0)) > 0
        }
    )


def build_monthly_invite_counter() -> Counter:
    return Counter(
        {
            staff.get("name", "Unknown"): int(staff.get("invite_accept", 0))
            for staff in _get_scope_data("monthly").values()
            if int(staff.get("invite_accept", 0)) > 0
        }
    )