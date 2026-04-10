from core.cache import app_state

from services.vrchat_client import get_all_vrc_staff_members

from .storage import leaderboard_data, save_leaderboard_data


def _ensure_section(section: str) -> None:
    if section not in leaderboard_data or not isinstance(leaderboard_data[section], dict):
        leaderboard_data[section] = {}


def _default_entry(name: str) -> dict:
    return {
        "name": name,
        "warn": 0,
        "kick": 0,
        "ban": 0,
        "invite": 0,
        "invite_accept": 0,
        "points": 0,
    }


def _ensure_staff_entry(section: str, user_id: str, display_name: str) -> None:
    _ensure_section(section)

    existing = leaderboard_data[section].get(user_id)

    defaults = _default_entry(display_name)

    if not isinstance(existing, dict):
        leaderboard_data[section][user_id] = defaults
        return

    # fill missing keys only
    for key, value in defaults.items():
        if key not in existing:
            existing[key] = value

    # always update name
    existing["name"] = display_name


async def sync_staff_from_vrc_group(force_refresh: bool = False) -> int:
    """
    Sync staff from VRChat group into leaderboard.

    Does NOT reset existing points.
    """

    staff_members = await get_all_vrc_staff_members(
        force_refresh=force_refresh
    )

    _ensure_section("staff")
    _ensure_section("monthly")

    count = 0

    for member in staff_members:

        user_id = member["user_id"]
        display_name = member["display_name"]

        _ensure_staff_entry("staff", user_id, display_name)
        _ensure_staff_entry("monthly", user_id, display_name)

        count += 1

    if count > 0:

        app_state.leaderboard_dirty = True
        save_leaderboard_data()

    return count