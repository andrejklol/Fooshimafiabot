import logging
from datetime import datetime, timezone, timedelta
from typing import Any

import discord

from core.cache import app_state
from core.config import ERROR_LOG_CHANNEL_ID, GUILD_ID, STAFF_ALERT_ORDER
from services.vrchat_client import (
    get_all_vrc_staff_members,
    vrc_user_is_staff,
)
from services.offenders import (
    add_warn,
    add_kick,
    add_ban,
    get_triggered_thresholds,
    get_highest_action,
    send_repeat_alert,
)

from .scoring import get_action_score
from .storage import leaderboard_data, save_leaderboard_data

log = logging.getLogger("leaderboard_processors")

_MOD_ACTIONS = {"warn", "kick", "ban"}
_SUPPORTED_ACTIONS = {"warn", "kick", "ban", "invite", "invite_accept"}

ARCHIVE_GRACE_PERIOD = timedelta(hours=24)
ARCHIVE_WARNING_AFTER = timedelta(hours=12)

# cleaned role list
DISCORD_STAFF_ROLE_NAMES = {
    "godfooshi",
    "underboss",
    "consigliere",
    "capo",
    "soldier",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _send_archive_log_message(bot, title: str, description: str) -> None:
    if not bot:
        return

    try:
        channel = bot.get_channel(ERROR_LOG_CHANNEL_ID)

        if not channel and GUILD_ID:
            guild = bot.get_guild(GUILD_ID)
            if guild:
                channel = guild.get_channel(ERROR_LOG_CHANNEL_ID)

        if not channel:
            return

        embed = discord.Embed(
            title=title,
            description=description,
            color=discord.Color.orange(),
            timestamp=datetime.now(timezone.utc),
        )

        await channel.send(embed=embed)

    except Exception as exc:
        log.warning("Failed to send archive log message: %r", exc)


def _ensure_section(section: str) -> None:
    if section not in leaderboard_data:
        leaderboard_data[section] = {}


def _ensure_archive_section() -> None:
    _ensure_section("archive")


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _build_default_staff_entry(staff_id: str, staff_name: str) -> dict:
    return {
        "id": staff_id,
        "name": staff_name,
        "warn": 0,
        "kick": 0,
        "ban": 0,
        "invite": 0,
        "invite_accept": 0,
        "points": 0,
    }


def _ensure_staff(section: str, staff_id: str, staff_name: str) -> bool:
    _ensure_section(section)

    if staff_id not in leaderboard_data[section]:
        leaderboard_data[section][staff_id] = _build_default_staff_entry(
            staff_id,
            staff_name,
        )
        return True

    return False


def _build_vrc_to_discord_map() -> dict[str, int]:
    mapping = {}

    for action_groups in STAFF_ALERT_ORDER.values():
        for _, members in action_groups:

            for member in members:

                vrchat_user_id = str(member.get("vrchat_user_id", "")).strip()
                discord_id = member.get("discord_id")

                if vrchat_user_id and discord_id:
                    mapping[vrchat_user_id] = int(discord_id)

    return mapping


# improved matching
def _member_has_discord_staff_role(member) -> bool:

    roles = [
        str(role.name).lower().strip()
        for role in getattr(member, "roles", [])
    ]

    for user_role in roles:

        for staff_role in DISCORD_STAFF_ROLE_NAMES:

            if staff_role in user_role:
                return True

    return False


# prevents false archive spikes
def _should_skip_archive(previous_staff_count: int, fetched_staff_count: int) -> bool:

    if previous_staff_count <= 0:
        return False

    if fetched_staff_count == 0:
        return True

    # if more than 30% of staff suddenly disappear → skip archive
    if fetched_staff_count < max(3, int(previous_staff_count * 0.7)):

        log.warning(
            "Archive safety triggered: staff drop looked suspicious "
            "(previous=%s, fetched=%s)",
            previous_staff_count,
            fetched_staff_count,
        )

        return True

    return False


async def _archive_removed_staff(current_staff_ids: set[str], bot) -> bool:

    _ensure_section("staff")
    _ensure_archive_section()

    changed = False
    now = datetime.now(timezone.utc)

    if not hasattr(app_state, "archive_pending"):
        app_state.archive_pending = {}

    if not hasattr(app_state, "archive_warning_sent"):
        app_state.archive_warning_sent = {}

    guild = bot.get_guild(GUILD_ID) if bot else None

    vrc_to_discord = _build_vrc_to_discord_map()

    for staff_id in list(leaderboard_data["staff"].keys()):

        if staff_id in current_staff_ids:

            app_state.archive_pending.pop(staff_id, None)
            app_state.archive_warning_sent.pop(staff_id, None)

            continue

        display_name = leaderboard_data["staff"][staff_id]["name"]

        has_discord_role = False

        if guild:

            discord_id = vrc_to_discord.get(staff_id)

            if discord_id:

                member = guild.get_member(discord_id)

                if not member:
                    try:
                        member = await guild.fetch_member(discord_id)
                    except Exception:
                        member = None

                if member and _member_has_discord_staff_role(member):

                    has_discord_role = True

        # if they still have discord role → cancel archive timer
        if has_discord_role:

            app_state.archive_pending.pop(staff_id, None)
            app_state.archive_warning_sent.pop(staff_id, None)

            continue

        pending = app_state.archive_pending.get(staff_id)

        # start timer
        if not pending:

            app_state.archive_pending[staff_id] = {
                "missing_since": now.isoformat(),
            }

            log.info(
                "Archive timer started for %s",
                display_name,
            )

            continue

        missing_since = datetime.fromisoformat(
            pending["missing_since"]
        )

        elapsed = now - missing_since

        # 12h warning
        if (
            elapsed >= ARCHIVE_WARNING_AFTER
            and not app_state.archive_warning_sent.get(staff_id)
        ):

            await _send_archive_log_message(
                bot,
                "⚠ Staff Pending Archive",
                f"{display_name} missing roles for 12h",
            )

            app_state.archive_warning_sent[staff_id] = True

        # still in grace period
        if elapsed < ARCHIVE_GRACE_PERIOD:
            continue

        # archive
        entry = leaderboard_data["staff"].pop(staff_id)

        archived_entry = dict(entry)

        archived_entry["archived_at"] = _utc_now_iso()

        leaderboard_data["archive"][staff_id] = archived_entry

        app_state.archive_pending.pop(staff_id, None)
        app_state.archive_warning_sent.pop(staff_id, None)

        log.warning(
            "Archived staff after 24h grace: %s",
            display_name,
        )

        await _send_archive_log_message(
            bot,
            "📦 Staff Archived",
            f"{display_name} archived after 24h missing roles",
        )

        changed = True

    return changed


async def sync_all_vrc_staff_into_leaderboard(bot, force_refresh: bool = False) -> int:

    added = 0
    any_changed = False

    _ensure_section("staff")
    _ensure_archive_section()

    previous_staff_count = len(leaderboard_data["staff"])

    vrc_members = await get_all_vrc_staff_members(
        force_refresh=force_refresh
    )

    fetched_staff_count = len(vrc_members or [])

    current_staff_ids = set()

    for member in vrc_members or []:

        staff_id = str(
            member.get("user_id")
            or member.get("id")
            or ""
        )

        if not staff_id:
            continue

        current_staff_ids.add(staff_id)

        staff_name = (
            member.get("display_name")
            or member.get("displayName")
            or "Unknown"
        )

        was_missing = staff_id not in leaderboard_data["staff"]

        changed = _ensure_staff(
            "staff",
            staff_id,
            staff_name,
        )

        any_changed = changed or any_changed

        if was_missing:
            added += 1

    if _should_skip_archive(
        previous_staff_count,
        fetched_staff_count,
    ):

        log.warning(
            "Skipped archive pass "
            "(previous=%s, fetched=%s)",
            previous_staff_count,
            fetched_staff_count,
        )

    else:

        archived_changed = await _archive_removed_staff(
            current_staff_ids,
            bot,
        )

        any_changed = archived_changed or any_changed

    if any_changed:

        app_state.leaderboard_dirty = True

        save_leaderboard_data()

    return added
