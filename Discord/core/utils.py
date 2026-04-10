import asyncio
import json
import random
import re
import traceback
import unicodedata
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import discord

from core.cache import app_state
from core.config import ERROR_LOG_CHANNEL_ID, REPEAT_OFFENDER_FILE
from core.embeds import (
    ban_action_embed,
    info_embed,
    kick_action_embed,
    warning_embed,
    warn_action_embed,
)
from core.error_embed import build_error_embed


# ============================================================
# TIME HELPERS
# ============================================================

def now_ts() -> float:
    return datetime.now().timestamp()


def utc_now() -> datetime:
    return datetime.now(UTC)


def format_dt(dt: datetime | None) -> str:
    if not dt:
        return "N/A"

    try:
        return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return str(dt)


# ============================================================
# VRCHAT COOLDOWN
# ============================================================

def vrchat_cooldown_active() -> bool:
    banned_until = getattr(app_state, "api_banned_until", None)
    if banned_until is None:
        return False

    if isinstance(banned_until, datetime):
        return utc_now() < banned_until

    try:
        return now_ts() < float(banned_until)
    except Exception:
        return False


def format_remaining_cooldown() -> str:
    banned_until = getattr(app_state, "api_banned_until", None)
    if not banned_until:
        return "None"

    try:
        if isinstance(banned_until, datetime):
            remaining = int((banned_until - utc_now()).total_seconds())
        else:
            remaining = int(float(banned_until) - now_ts())
    except Exception:
        return "Unknown"

    return "Expired" if remaining <= 0 else f"{remaining}s"


# ============================================================
# TEXT HELPERS
# ============================================================

def _truncate_text(text: str | None, limit: int) -> str:
    if text is None:
        return "N/A"

    text = str(text)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _safe_code_block(text: str | None, limit: int = 1000) -> str:
    safe = _truncate_text(text or "N/A", limit).replace("```", "'''")
    return f"```py\n{safe}\n```"


def _split_text_chunks(text: str, limit: int) -> list[str]:
    return [text[i:i + limit] for i in range(0, len(text), limit)] if text else []


# ============================================================
# ERROR EMBED HELPERS
# ============================================================

def _get_error_type(error: Exception | str) -> str:
    return type(error).__name__ if isinstance(error, Exception) else "Error"


def _get_error_text(error: Exception | str) -> str:
    return str(error)


def _get_traceback_text(error: Exception | str) -> str | None:
    if not isinstance(error, Exception):
        return None

    try:
        tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        return tb.strip() or None
    except Exception:
        return None


def _get_exception_location(error: Exception | str) -> str | None:
    if not isinstance(error, Exception) or error.__traceback__ is None:
        return None

    try:
        tb = traceback.extract_tb(error.__traceback__)
        if not tb:
            return None

        last = tb[-1]
        return f"{Path(last.filename).name}:{last.lineno} in {last.name}"
    except Exception:
        return None


def _extract_ctx_details(ctx) -> dict[str, str]:
    details: dict[str, str] = {}

    try:
        author = getattr(ctx, "author", None)
        guild = getattr(ctx, "guild", None)
        channel = getattr(ctx, "channel", None)
        command = getattr(ctx, "command", None)

        if command:
            details["Command"] = getattr(command, "qualified_name", str(command))

        if author:
            details["User"] = f"{author} ({author.id})"

        if guild:
            details["Guild"] = f"{guild.name} ({guild.id})"

        if channel:
            channel_name = getattr(channel, "name", None) or str(channel)
            details["Channel"] = f"{channel_name} ({getattr(channel, 'id', 'unknown')})"
    except Exception:
        pass

    return details


def _normalize_extra(extra) -> dict[str, str]:
    if extra is None:
        return {}

    if isinstance(extra, dict):
        return {str(k): _truncate_text(str(v), 1000) for k, v in extra.items()}

    return {"Extra": _truncate_text(str(extra), 1000)}


# ============================================================
# EMBEDS
# ============================================================

def _build_error_embed(
        title: str,
        error: Exception | str,
        extra=None,
        severity: str = "error",
        ctx=None,
        trace_id: str | None = None,
) -> discord.Embed:
    severity = (severity or "error").lower()

    ctx_details = _extract_ctx_details(ctx) if ctx else {}
    normalized_extra = _normalize_extra(extra)

    username = None
    actor_id = None

    if "User" in ctx_details:
        username = ctx_details["User"]

    if "Actor ID" in normalized_extra:
        actor_id = normalized_extra.pop("Actor ID")

    if "actor_id" in normalized_extra and not actor_id:
        actor_id = normalized_extra.pop("actor_id")

    if "Username" in normalized_extra and not username:
        username = normalized_extra.pop("Username")

    if "username" in normalized_extra and not username:
        username = normalized_extra.pop("username")

    details = {
        "Type": _get_error_type(error),
        "Details": _get_error_text(error),
    }

    location = _get_exception_location(error)
    if location:
        details["Location"] = location

    traceback_text = _get_traceback_text(error)
    if traceback_text:
        for i, chunk in enumerate(_split_text_chunks(traceback_text, 900)[:2], start=1):
            details["Traceback" if i == 1 else f"Traceback {i}"] = chunk

    details.update(normalized_extra)
    details.update(ctx_details)

    if severity == "warning":
        embed = warning_embed(title, _truncate_text(_get_error_text(error), 300))
        embed.timestamp = utc_now()

        if username:
            embed.add_field(name="User", value=f"`{_truncate_text(username, 256)}`", inline=True)

        if actor_id:
            embed.add_field(name="Actor ID", value=f"`{_truncate_text(actor_id, 256)}`", inline=True)

        if trace_id:
            embed.add_field(name="Trace ID", value=f"`{_truncate_text(trace_id, 100)}`", inline=False)

        if location:
            embed.add_field(name="Location", value=_truncate_text(location, 256), inline=False)

        for key, value in details.items():
            if key in {"Details", "Type", "Location", "User", "Actor ID", "Username", "username", "actor_id"}:
                continue
            embed.add_field(
                name=_truncate_text(str(key), 256),
                value=_truncate_text(str(value), 1000),
                inline=False,
            )

        embed.set_footer(text="Fooshi Error Logger")
        return embed

    if severity == "info":
        embed = info_embed(title, _truncate_text(_get_error_text(error), 300))
        embed.timestamp = utc_now()

        if username:
            embed.add_field(name="User", value=f"`{_truncate_text(username, 256)}`", inline=True)

        if actor_id:
            embed.add_field(name="Actor ID", value=f"`{_truncate_text(actor_id, 256)}`", inline=True)

        if trace_id:
            embed.add_field(name="Trace ID", value=f"`{_truncate_text(trace_id, 100)}`", inline=False)

        if location:
            embed.add_field(name="Location", value=_truncate_text(location, 256), inline=False)

        for key, value in details.items():
            if key in {"Details", "Type", "Location", "User", "Actor ID", "Username", "username", "actor_id"}:
                continue
            embed.add_field(
                name=_truncate_text(str(key), 256),
                value=_truncate_text(str(value), 1000),
                inline=False,
            )

        embed.set_footer(text="Fooshi Error Logger")
        return embed

    return build_error_embed(
        title=title,
        description=_truncate_text(_get_error_text(error), 3000),
        username=username,
        actor_id=actor_id,
        trace_id=trace_id,
        extra=details,
    )


def _build_debug_embed(
        title: str,
        message: str,
        trace_id: str | None = None,
        ctx=None,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"ℹ️ {title}",
        color=discord.Color.blurple(),
        timestamp=utc_now(),
    )

    if trace_id:
        embed.add_field(name="Trace ID", value=f"`{_truncate_text(trace_id, 100)}`", inline=True)

    embed.add_field(name="Details", value=_safe_code_block(message, 1000), inline=False)

    if ctx:
        for key, value in _extract_ctx_details(ctx).items():
            embed.add_field(name=key, value=_truncate_text(value, 512), inline=False)

    embed.set_footer(text="Fooshi Debug Logger")
    return embed


# ============================================================
# DISCORD LOG CHANNEL
# ============================================================

async def _fetch_log_channel() -> discord.TextChannel | None:
    if not app_state.bot or not ERROR_LOG_CHANNEL_ID:
        return None

    channel = app_state.bot.get_channel(ERROR_LOG_CHANNEL_ID)
    if channel:
        return channel

    try:
        fetched = await app_state.bot.fetch_channel(ERROR_LOG_CHANNEL_ID)
        return fetched if isinstance(fetched, discord.TextChannel) else None
    except Exception:
        return None


# ============================================================
# RESPONSE / LOG SENDERS
# ============================================================

async def respond(ctx, content=None, embed=None, ephemeral=False) -> None:
    try:
        interaction = getattr(ctx, "interaction", None)

        if interaction:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    content=content,
                    embed=embed,
                    ephemeral=ephemeral,
                )
            else:
                await interaction.followup.send(
                    content=content,
                    embed=embed,
                    ephemeral=ephemeral,
                )
        else:
            await ctx.send(content=content, embed=embed)

    except Exception as exc:
        try:
            await send_error_log(
                "Response Error",
                exc,
                extra={
                    "content": _truncate_text(content, 300),
                    "had_embed": embed is not None,
                    "ephemeral": ephemeral,
                },
                ctx=ctx,
                trace_id="respond",
            )
        except Exception:
            pass


async def send_error_log(
        title: str,
        error: Exception | str,
        extra=None,
        severity: str = "error",
        ctx=None,
        trace_id: str | None = None,
) -> None:
    try:
        channel = await _fetch_log_channel()
        if channel is None:
            return

        embed = _build_error_embed(
            title=title,
            error=error,
            extra=extra,
            severity=severity,
            ctx=ctx,
            trace_id=trace_id,
        )
        await channel.send(embed=embed)

    except Exception:
        pass


async def send_debug_log(
        title: str,
        message: str,
        trace_id: str | None = None,
        ctx=None,
) -> None:
    try:
        channel = await _fetch_log_channel()
        if channel is None:
            return

        await channel.send(
            embed=_build_debug_embed(
                title=title,
                message=message,
                trace_id=trace_id,
                ctx=ctx,
            )
        )
    except Exception:
        pass


# ============================================================
# RATE LIMIT HELPERS
# ============================================================

_RETRY_AFTER_PATTERNS = [
    r'"retry_after":\s*(\d+)',
    r"'retry_after':\s*(\d+)",
    r'"Retry-After":\s*"(\d+)"',
    r"'Retry-After': '(\d+)'",
    r"Retry-After[:=]\s*(\d+)",
]


def _extract_retry_after_seconds(error_text: str) -> int | None:
    for pattern in _RETRY_AFTER_PATTERNS:
        if m := re.search(pattern, error_text, re.IGNORECASE):
            try:
                return int(m.group(1))
            except ValueError:
                pass
    return None


def _is_rate_limit_error(error_text: str) -> bool:
    text = (error_text or "").lower()
    return (
            "429" in text
            or "rate limit" in text
            or "too many requests" in text
            or "slow down" in text
    )


async def handle_rate_limit(retry_after: int | None = None) -> None:
    attempts = getattr(app_state, "api_rate_limit_attempts", 0) + 1
    app_state.api_rate_limit_attempts = attempts

    base = retry_after or 30
    wait_time = int(min(base * (2 ** (attempts - 1)), 300) * random.uniform(0.85, 1.15))

    app_state.api_retry_after = wait_time
    app_state.api_banned_until = now_ts() + wait_time


def reset_rate_limit_backoff() -> None:
    app_state.api_rate_limit_attempts = 0
    app_state.api_retry_after = None
    app_state.api_banned_until = None


async def run_blocking(func, *args, **kwargs):
    loop = asyncio.get_running_loop()

    if vrchat_cooldown_active():
        banned_until = getattr(app_state, "api_banned_until", None)
        try:
            if isinstance(banned_until, datetime):
                remaining = int((banned_until - utc_now()).total_seconds())
            else:
                remaining = int(float(banned_until) - now_ts())
        except Exception:
            remaining = 0

        if remaining > 0:
            await asyncio.sleep(remaining)

    try:
        result = await loop.run_in_executor(None, partial(func, *args, **kwargs))
        reset_rate_limit_backoff()
        return result

    except Exception as exc:
        err = str(exc)
        app_state.last_api_error = err

        if _is_rate_limit_error(err):
            await handle_rate_limit(_extract_retry_after_seconds(err))

        raise


# ============================================================
# LOG FILTERING
# ============================================================

def get_entry_id(entry) -> str:
    if entry_id := getattr(entry, "id", None):
        return str(entry_id)

    created_at = getattr(entry, "created_at", None)
    created_text = created_at.isoformat() if created_at else "unknown_time"
    actor = getattr(entry, "actor_display_name", "unknown_actor")
    event = getattr(entry, "event_type", "unknown_event")
    target = getattr(entry, "target_id", "unknown_target")
    return f"{created_text}|{actor}|{event}|{target}"


def should_process_log(entry) -> bool:
    return get_entry_id(entry) not in app_state.processed_log_ids


def should_count_for_leaderboard(entry) -> bool:
    created_at = getattr(entry, "created_at", None)
    startup = getattr(app_state, "startup_timestamp", None)

    if startup and created_at and created_at < startup:
        return False

    return should_process_log(entry)


# ============================================================
# ACTION DETECTION
# ============================================================

_ACTION_KEYWORDS: list[tuple[str, set[str]]] = [
    ("warn", {"warn", "warning", "warned"}),
    ("kick", {"kick", "kicked"}),
    ("ban", {"ban", "banned"}),
    ("invite", {"invite", "invited"}),
]


def _match_action_keywords(text: str) -> str | None:
    lower = text.lower()
    for action, keywords in _ACTION_KEYWORDS:
        if any(kw in lower for kw in keywords):
            return action
    return None


def classify_moderation_action(entry) -> str | None:
    event = str(getattr(entry, "event_type", "") or "")
    desc = str(getattr(entry, "description", "") or "")
    return _match_action_keywords(event) or _match_action_keywords(desc)


# ============================================================
# DISPLAY
# ============================================================

def clean_display_name(name) -> str:
    if not name:
        return "Unknown"

    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(c for c in text if not unicodedata.combining(c) and c.isprintable())
    return re.sub(r"\s+", " ", text).strip() or "Unknown"


def _get_first_attr(entry, *names, default=None):
    for name in names:
        value = getattr(entry, name, None)
        if value not in (None, "", "None"):
            return value
    return default


def _profile_url(user_id: str | None) -> str | None:
    if not user_id:
        return None

    user_id = str(user_id).strip()
    if not user_id.startswith("usr_"):
        return None

    return f"https://vrchat.com/home/user/{user_id}"


def _extract_target_name_from_description(description: str | None) -> str | None:
    if not description:
        return None

    text = str(description).strip()

    patterns = [
        r'unassigned the role\s+"[^"]+"\s+from\s+(.+)$',
        r'assigned the role\s+"[^"]+"\s+to\s+(.+)$',
        r'warned\s+(.+)$',
        r'kicked\s+(.+)$',
        r'banned\s+(.+)$',
        r'invited\s+(.+)$',
        r'removed\s+(.+)$',
        r'added\s+(.+)$',
        r'instance kick for\s+(.+)$',
        r'instance ban for\s+(.+)$',
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            name = re.sub(r"\s+\([^)]*\)$", "", name).strip()
            return name or None

    return None


def _extract_role_name_from_description(description: str | None) -> str | None:
    if not description:
        return None

    text = str(description).strip()

    patterns = [
        r'group role\s+(.+?)\s+updated\s+by\s+.+$',
        r'assigned the role\s+"([^"]+)"\s+to\s+.+$',
        r'unassigned the role\s+"([^"]+)"\s+from\s+.+$',
        r'created the role\s+"([^"]+)"',
        r'deleted the role\s+"([^"]+)"',
        r'updated the role\s+"([^"]+)"',
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            role_name = match.group(1).strip().strip('"')
            return role_name or None

    return None


def _get_cached_target_name(user_id: str | None) -> str | None:
    if not user_id:
        return None

    user_id = str(user_id).strip()
    if not user_id:
        return None

    caches = [
        getattr(app_state, "target_name_cache", None),
        getattr(app_state, "user_name_cache", None),
        getattr(app_state, "vrchat_name_cache", None),
        getattr(app_state, "vrchat_user_cache", None),
        getattr(app_state, "vrchat_user_lookup", None),
    ]

    for cache in caches:
        if not cache:
            continue

        try:
            value = cache.get(user_id)
        except Exception:
            continue

        if isinstance(value, str) and value.strip():
            return value.strip()

        if isinstance(value, dict):
            for key in ("display_name", "displayName", "username", "name"):
                nested = value.get(key)
                if isinstance(nested, str) and nested.strip():
                    return nested.strip()

    if not user_id.startswith("usr_"):
        return None

    users_api = getattr(app_state, "vrc_users_api", None)
    if users_api is None:
        return None

    try:
        user = users_api.get_user(user_id)

        if user is None:
            return None

        display_name = (
                getattr(user, "display_name", None)
                or getattr(user, "displayName", None)
                or getattr(user, "username", None)
                or getattr(user, "name", None)
        )

        if not display_name:
            return None

        display_name = str(display_name).strip()
        if not display_name:
            return None

        cache = getattr(app_state, "vrchat_user_cache", None)
        if cache is None or not isinstance(cache, dict):
            app_state.vrchat_user_cache = {}
            cache = app_state.vrchat_user_cache

        cache[user_id] = display_name

        target_cache = getattr(app_state, "target_name_cache", None)
        if isinstance(target_cache, dict):
            target_cache[user_id] = display_name

        return display_name

    except Exception:
        return None


def _event_style(event_type: str) -> tuple[int, str]:
    e = (event_type or "").lower()

    if "warn" in e:
        return 0xF1C40F, "⚠️"

    if "kick" in e:
        return 0xE67E22, "👢"

    if "ban" in e:
        return 0xE74C3C, "🔨"

    if "invite" in e:
        return 0x2ECC71, "✉️"

    if "role" in e and "unassign" in e:
        return 0x9B59B6, "➖"

    if "role" in e and "assign" in e:
        return 0x3498DB, "➕"

    if "join" in e:
        return 0x2ECC71, "✅"

    if "leave" in e:
        return 0x95A5A6, "🚪"

    if "instance create" in e or "instance created" in e:
        return 0x5865F2, "ℹ️"

    return 0x5865F2, "ℹ️"


def _name_link(name: str, user_id: str) -> str:
    url = _profile_url(user_id)

    if not url:
        return f"**{name}**"

    return f"[**{name}**]({url})"


def _id_display(user_id: str) -> str:
    return f"`{user_id}`"


def _is_world_target(target_id: str | None) -> bool:
    return str(target_id or "").startswith("wrld_")


def _is_group_instance_target(target_id: str | None) -> bool:
    text = str(target_id or "")
    return text.startswith("wrld_") and "group(" in text


def _resolve_target_name(entry, description, target_id, event_type):
    target_name = _get_first_attr(
        entry,
        "target_display_name",
        "target_name",
        "target_username",
    )

    if not target_name:
        target_name = _get_cached_target_name(target_id)

    is_role_target = str(target_id).startswith("grol_") or "role" in str(event_type).lower()

    if is_role_target and not target_name:
        target_name = _extract_role_name_from_description(description)

    if not target_name and not is_role_target:
        parsed = _extract_target_name_from_description(description)
        if parsed:
            target_name = parsed

    if _is_group_instance_target(target_id):
        return "Group Instance"

    if _is_world_target(target_id):
        return "World Instance"

    if not target_name:
        return str(target_id)

    return clean_display_name(target_name)


def build_log_embed(entry, leaderboard_ignored: bool = False) -> discord.Embed:
    event_type_raw = str(getattr(entry, "event_type", "Unknown") or "Unknown")
    event_type = event_type_raw.lower()
    title = event_type_raw.replace(".", " ").title()
    description = getattr(entry, "description", None) or "No description"

    actor_id = str(_get_first_attr(entry, "actor_id", "actor_user_id", default="Unknown"))
    target_id = str(_get_first_attr(entry, "target_id", "target_user_id", default="Unknown"))

    actor_name = _get_first_attr(
        entry,
        "actor_display_name",
        "actor_name",
        "actor_username",
    )

    if not actor_name:
        actor_name = _get_cached_target_name(actor_id)

    actor_name = clean_display_name(actor_name or actor_id)

    is_role_target = str(target_id).startswith("grol_") or "role" in event_type
    target_name = _resolve_target_name(entry, description, target_id, event_type)

    color, icon = _event_style(event_type)

    embed = discord.Embed(
        title=f"{icon} {title}",
        color=color,
    )

    embed.add_field(
        name="Actor",
        value=(
            f"**Name**\n"
            f"{_name_link(actor_name, actor_id)}\n\n"
            f"**ID**\n"
            f"{_id_display(actor_id)}"
        ),
        inline=True,
    )

    if is_role_target:
        target_value = f"**Role**\n**{clean_display_name(target_name or 'Unknown Role')}**"
        if target_id and target_id != "Unknown":
            target_value += f"\n\n**Role ID**\n{_id_display(target_id)}"
    else:
        target_value = (
            f"**Name**\n"
            f"{_name_link(clean_display_name(target_name), target_id)}\n\n"
            f"**ID**\n"
            f"{_id_display(target_id)}"
        )

    embed.add_field(
        name="Target",
        value=target_value,
        inline=True,
    )

    if leaderboard_ignored:
        embed.add_field(
            name="Leaderboard",
            value="⚠️ ignored (staff on staff)",
            inline=False,
        )

    embed.add_field(
        name="Details",
        value=_truncate_text(description, 1000),
        inline=False,
    )

    if created_at := getattr(entry, "created_at", None):
        embed.timestamp = created_at

    return embed


def format_counter(counter_obj, limit: int = 3) -> str:
    if not counter_obj:
        return "No data"

    medals = ["🥇", "🥈", "🥉"]
    lines = [
        f"{medals[i] if i < 3 else '•'} {clean_display_name(name)} — `{count}`"
        for i, (name, count) in enumerate(counter_obj.most_common(limit))
    ]
    return "\n".join(lines)


# ============================================================
# HISTORY
# ============================================================

HISTORY_PATH = REPEAT_OFFENDER_FILE


def infer_action_from_description(desc) -> str | None:
    return _match_action_keywords(str(desc))


def normalize_history_data(data) -> dict:
    if not isinstance(data, dict):
        data = {}

    fixed: dict[str, list] = {}
    for user, entries in data.get("actions", {}).items():
        if not isinstance(entries, list):
            continue

        clean = [
            {
                "action": action,
                "timestamp": e.get("timestamp"),
                "actor": e.get("actor"),
                "description": e.get("description"),
            }
            for e in entries
            if isinstance(e, dict) and (action := infer_action_from_description(e.get("description")))
        ]

        if clean:
            fixed[user] = clean

    return {
        "actions": fixed,
        "alerted_keys": data.get("alerted_keys", []),
        "target_name_cache": data.get("target_name_cache", {}),
    }


def load_history() -> dict:
    if not HISTORY_PATH.exists():
        data: dict = {"actions": {}, "alerted_keys": [], "target_name_cache": {}}
        save_history(data)
        return data

    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        raw = {}

    cleaned = normalize_history_data(raw)
    save_history(cleaned)
    return cleaned


def save_history(data) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(normalize_history_data(data), f, indent=2, ensure_ascii=False)


def add_history_entry(history, user_id, actor, description, timestamp) -> None:
    action = infer_action_from_description(description)
    if not action:
        return

    history.setdefault("actions", {}).setdefault(user_id, []).append(
        {
            "action": action,
            "timestamp": timestamp,
            "actor": actor,
            "description": description,
        }
    )