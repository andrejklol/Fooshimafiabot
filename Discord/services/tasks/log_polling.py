from datetime import timezone

from core.cache import app_state
from core.config import GROUP_ID, LOG_CHANNEL_ID, RECENT_LOG_FETCH_COUNT
from core.utils import build_log_embed, run_blocking, send_error_log

from services.leaderboard.processors import process_audit_log_entry
from services.tasks.autosave import autosave_if_dirty
from services.tasks.monthly_reset import check_monthly_reset


def _mark_log_processed(entry) -> None:
    log_id = getattr(entry, "id", None)
    if log_id:
        app_state.processed_log_ids.add(log_id)


def _was_log_processed(entry) -> bool:
    log_id = getattr(entry, "id", None)
    return bool(log_id and log_id in app_state.processed_log_ids)


def _normalize_entry_created_at(entry):
    created_at = getattr(entry, "created_at", None)
    if not created_at:
        return None

    if getattr(created_at, "tzinfo", None) is None:
        return created_at.replace(tzinfo=timezone.utc)

    return created_at.astimezone(timezone.utc)


def _normalize_utc(dt):
    if not dt:
        return None

    if getattr(dt, "tzinfo", None) is None:
        return dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


async def _get_log_channel():
    if not app_state.bot or not LOG_CHANNEL_ID:
        return None

    channel = app_state.bot.get_channel(LOG_CHANNEL_ID)
    if channel is not None:
        return channel

    try:
        return await app_state.bot.fetch_channel(LOG_CHANNEL_ID)
    except Exception:
        return None


async def _fetch_recent_logs():
    logs = await run_blocking(
        app_state.vrc_groups_api.get_group_audit_logs,
        group_id=GROUP_ID,
        n=RECENT_LOG_FETCH_COUNT,
        offset=0,
    )
    return getattr(logs, "results", []) or []


def _collect_new_logs(results, startup_ts):
    new_logs = []

    for entry in results:
        created_at = _normalize_entry_created_at(entry)

        if not created_at:
            continue

        if _was_log_processed(entry):
            continue

        if created_at < startup_ts:
            continue

        new_logs.append(entry)

    return new_logs


def _get_newest_created_at(entries):
    newest_created_at = None

    for entry in entries:
        created_at = _normalize_entry_created_at(entry)
        if created_at and (newest_created_at is None or created_at > newest_created_at):
            newest_created_at = created_at

    return newest_created_at


async def _send_log_embeds(channel, processed_entries):
    for entry, leaderboard_ignored in processed_entries:
        embed = build_log_embed(
            entry,
            leaderboard_ignored=leaderboard_ignored,
        )
        await channel.send(embed=embed)


async def check_logs_once() -> None:
    if not app_state.vrc_groups_api or not app_state.startup_timestamp:
        return

    try:
        await check_monthly_reset()

        results = await _fetch_recent_logs()
        startup_ts = _normalize_utc(app_state.startup_timestamp)

        new_logs = _collect_new_logs(results, startup_ts)
        if not new_logs:
            return

        ordered = list(reversed(new_logs))
        processed_entries: list[tuple[object, bool]] = []

        for entry in ordered:
            matched_supported_action, leaderboard_ignored = await process_audit_log_entry(entry)
            _mark_log_processed(entry)

            processed_entries.append(
                (entry, leaderboard_ignored if matched_supported_action else False)
            )

        newest_created_at = _get_newest_created_at(ordered)
        if newest_created_at is not None:
            app_state.last_log_received_at = newest_created_at

        channel = await _get_log_channel()
        if channel:
            await _send_log_embeds(channel, processed_entries)

        await autosave_if_dirty()

    except Exception as exc:
        await send_error_log("Log Polling Error", exc)
