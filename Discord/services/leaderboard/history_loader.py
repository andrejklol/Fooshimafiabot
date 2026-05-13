import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from core.cache import app_state
from core.config import GROUP_ID, HISTORY_BATCH_SIZE
from core.utils import run_blocking, send_error_log

from .processors import _get_action_type, process_audit_log_entry
from .storage import (
    reset_leaderboard_data,
    reset_monthly_leaderboard_data,
    save_leaderboard_data,
)


def _parse_entry_timestamp(entry: Any) -> datetime | None:
    raw = None
    for attr in ("created_at", "createdAt", "timestamp", "time"):
        raw = getattr(entry, attr, None)
        if raw:
            break

    if not raw:
        return None

    if isinstance(raw, datetime):
        dt = raw
    else:
        text = str(raw).strip()
        try:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            dt = datetime.fromisoformat(text)
        except Exception:
            return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def _get_current_month_start_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc)


def _should_include_entry_for_monthly(entry: Any) -> bool:
    entry_dt = _parse_entry_timestamp(entry)
    if entry_dt is None:
        return False
    return entry_dt >= _get_current_month_start_utc()


# ============================================================
# MAIN LOADER
# ============================================================

async def load_full_history(
    limit: int = 1000,
    rebuild: bool = False,
    monthly_only: bool = False,
) -> dict[str, Any]:
    started = time.monotonic()

    fetched = 0
    counted_total = 0
    ignored_total = 0
    skipped_unmatched = 0

    # Local diagnostic counter tracking
    metrics = {
        "warn": 0,
        "kick": 0,
        "ban": 0,
        "invite": 0,
        "invite_accept": 0,
    }

    try:
        if not app_state.vrc_groups_api:
            raise RuntimeError("VRChat groups API is not ready.")

        if rebuild:
            if monthly_only:
                reset_monthly_leaderboard_data()
            else:
                reset_leaderboard_data()

        offset = 0

        while fetched < limit:
            logs = await run_blocking(
                app_state.vrc_groups_api.get_group_audit_logs,
                group_id=GROUP_ID,
                n=min(HISTORY_BATCH_SIZE, limit - fetched),
                offset=offset,
            )

            results = getattr(logs, "results", None)
            if not results:
                break

            batch = list(results)
            ordered = list(reversed(batch))

            for entry in ordered:
                if monthly_only and not _should_include_entry_for_monthly(entry):
                    continue

                normalized_action = _get_action_type(entry)

                # Process event entirely through the main core engine processor
                # This inherently takes care of matching, archiving tracking, and metrics calculation safely.
                matched_supported_action, leaderboard_ignored = await process_audit_log_entry(
                    entry,
                    save=False,  # Defer disk writes until batch loops conclude
                )

                if not matched_supported_action:
                    skipped_unmatched += 1
                    continue

                counted_total += 1
                if leaderboard_ignored:
                    ignored_total += 1

                if normalized_action in metrics:
                    metrics[normalized_action] += 1

            fetched += len(batch)
            offset += len(batch)

            await asyncio.sleep(0.15)

        # Commit structural data safely across state once logic is completed
        save_leaderboard_data()
        try:
            app_state.leaderboard_dirty = True
        except Exception:
            pass

        elapsed_seconds = time.monotonic() - started

        return {
            "fetched": fetched,
            "counted_total": counted_total,
            "ignored_total": ignored_total,
            "warns": metrics["warn"],
            "kicks": metrics["kick"],
            "bans": metrics["ban"],
            "invites": metrics["invite"],
            "invite_accepts": metrics["invite_accept"],
            "skipped_unmatched": skipped_unmatched,
            "skipped": max(fetched - counted_total, 0),
            "elapsed_seconds": elapsed_seconds,
        }

    except Exception as exc:
        await send_error_log("Load Full History Error", exc)
        raise
