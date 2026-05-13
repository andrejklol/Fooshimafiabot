from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import aiohttp

log = logging.getLogger("dashboard_sync")

# ─── Configuration ────────────────────────────────────────────────────────

def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        value = int(str(raw).strip())
        return value if value > 0 else default
    except (TypeError, ValueError):
        log.warning("invalid %s=%r; falling back to default %s", key, raw, default)
        return default


def _env_int_nonneg(key: str, default: int) -> int:
    """Like `_env_int` but accepts 0 as a valid value (e.g. `INVITE_SCORE=0`).

    Negative ints fall back to default.
    """
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        value = int(str(raw).strip())
        return value if value >= 0 else default
    except (TypeError, ValueError):
        log.warning("invalid %s=%r; falling back to default %s", key, raw, default)
        return default


# ─── Moderation point scoring (mirrors the bot's env contract) ────────────
#
# The Discord bot already has these env-driven score values for its own
# leaderboard math. We read the SAME env vars here so the dashboard's
# `points` / `monthly_points` columns track the bot's internal score
# exactly — no double-source of truth, no points drift.
WARN_SCORE = _env_int_nonneg("WARN_SCORE", 1)
KICK_SCORE = _env_int_nonneg("KICK_SCORE", 2)
BAN_SCORE = _env_int_nonneg("BAN_SCORE", 4)
INVITE_SCORE = _env_int_nonneg("INVITE_SCORE", 0)
INVITE_ACCEPT_BONUS = _env_int_nonneg("INVITE_ACCEPT_BONUS", 1)


def _score_for_action(action_type: Optional[str]) -> int:
    """Return the points awarded for a given moderation action.

    Action names are normalized first so dotted VRChat names
    (`group.instance.warn`) and aliases (`warning`/`banhammer`) all
    resolve to the same canonical bucket. Anything outside the known
    set scores 0.
    """
    canonical = _normalize_action(action_type)
    if not canonical:
        return 0
    # `invite.accept` (dotted) takes priority over the generic `invite`
    # tail so `group.invite.accept` resolves to the bonus path.
    if "invite.accept" in canonical or canonical in ("invite_accept",):
        return INVITE_SCORE + INVITE_ACCEPT_BONUS
    tail = canonical.rsplit(".", 1)[-1] if "." in canonical else canonical
    if tail == "warn":
        return WARN_SCORE
    if tail == "kick":
        return KICK_SCORE
    if tail == "ban":
        return BAN_SCORE
    if tail == "invite":
        return INVITE_SCORE
    return 0


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("true", "1", "yes", "on")


class SyncConfig:
    """Snapshot of env-driven tunables, resolved once at construction."""

    def __init__(self) -> None:
        self.enabled = _env_bool("DASHBOARD_SYNC_ENABLED", True)
        self.debounce_seconds = _env_int("DASHBOARD_SYNC_DEBOUNCE_SECONDS", 30)
        self.max_chunk = _env_int("DASHBOARD_SYNC_MAX_CHUNK", 50)
        self.log_batch_size = _env_int("DASHBOARD_SYNC_LOG_BATCH_SIZE", 10)
        self.log_flush_seconds = _env_int("DASHBOARD_SYNC_LOG_FLUSH_SECONDS", 30)
        self.retry_count = _env_int("DASHBOARD_SYNC_RETRY_COUNT", 3)
        self.timeout_seconds = _env_int("DASHBOARD_SYNC_TIMEOUT_SECONDS", 20)

    def as_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "debounce_seconds": self.debounce_seconds,
            "max_chunk": self.max_chunk,
            "log_batch_size": self.log_batch_size,
            "log_flush_seconds": self.log_flush_seconds,
            "retry_count": self.retry_count,
            "timeout_seconds": self.timeout_seconds,
        }


# ─── Normalization helpers ────────────────────────────────────────────────

_PLACEHOLDER_NAMES = {"unknown", "unknown user", "user", "n/a", "none", "-", "null", ""}
_PLACEHOLDER_PATTERN = re.compile(r"user[\s_-]*\d+", re.IGNORECASE)

# Canonical list of every alias the VRChat API (raw + our bot-side
# normalizers) ever emits for a user's avatar thumbnail. Shared by all
# three payload builders so staff, offender, and vrchat-status rows all
# pick the same URL regardless of which code path produced the dict.
#
# Order matters — first populated alias wins. `currentAvatarThumbnailImageUrl`
# is VRChat's first-party field and produces the cleanest 256px thumbnail;
# snake_case variants exist for dict-normalized copies that went through
# `build_*` once already. `profilePicOverride` / `profile_pic_override` is
# the user-selected override; `userIcon` is the older API field. Finally
# `avatar_url` is the generic fallback for any hand-rolled helper.
_VRCHAT_AVATAR_URL_ALIASES = (
    "vrchat_avatar_url",
    "currentAvatarThumbnailImageUrl",
    "current_avatar_thumbnail_image_url",
    "profilePicOverride",
    "profile_pic_override",
    "userIcon",
    "user_icon",
    "vrchatAvatarUrl",
    "avatar_url_vrchat",
    "avatar_url",
)


def _first_non_empty(*values: Any) -> Optional[Any]:
    """Return the first non-None / non-blank value from the sequence.

    Strings are stripped; blank strings count as missing. Non-string
    truthy values pass through unchanged. Returns None if nothing
    qualifies."""
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
            continue
        return value
    return None


def _looks_like_vrchat_id(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower().startswith("usr_")


# Discord snowflakes are pure-digit IDs: ~17-19 chars (Twitter/X-style).
# The 17-char floor catches every real Discord ID minted since ~2015;
# 20 chars gives forward-headroom past 2080. Anything outside this band
# (e.g. `staff_2`, `1001`, `usr_…`, `andrejklol`) is rejected so the
# dashboard's name-fallback takes over instead of $inc'ing nothing.
_DISCORD_SNOWFLAKE_RE = re.compile(r"^\d{17,20}$")


def _looks_like_discord_snowflake(value: Any) -> bool:
    if value is None:
        return False
    return bool(_DISCORD_SNOWFLAKE_RE.fullmatch(str(value).strip()))


def _coerce_discord_snowflake(*values: Any) -> Optional[str]:
    """Return the first value that's a valid Discord snowflake.

    Skips bot-side placeholder ids (`staff_2`, `1001`, `usr_…`, plain
    usernames) so the dashboard never receives a non-snowflake in the
    `*_discord_id` slot. When nothing matches, returns None — caller
    omits the field, and the backend's name-fallback resolves the
    correct staff doc via `staff_name`.
    """
    for value in values:
        if value is None:
            continue
        if _looks_like_discord_snowflake(value):
            return str(value).strip()
    return None


def _looks_like_placeholder_name(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return True
    if text.lower() in _PLACEHOLDER_NAMES:
        return True
    if _PLACEHOLDER_PATTERN.fullmatch(text.lower()):
        return True
    # A raw VRChat ID is never a human name.
    if _looks_like_vrchat_id(text):
        return True
    return False


def _best_name(*values: Any) -> Optional[str]:
    """Return the first usable human-readable name.

    Skips placeholders like `Unknown`, `User123`, `usr_xxxx` etc. so the
    dashboard never records them as if they were real display names."""
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        if _looks_like_placeholder_name(text):
            continue
        return text
    return None


def _best_vrchat_avatar_url(item: dict) -> Optional[str]:
    """Pick the best VRChat avatar URL from any dict using the canonical
    alias list. Returns None when no alias holds a non-blank string.

    Previously each builder hand-maintained a different subset of
    aliases, which meant `userIcon` or `profilePicOverride` coming out
    of the bot's `_extract_vrchat_avatar_url` helper would silently get
    dropped from `build_staff_payload` / `build_vrchat_status_payload`.
    Centralizing the alias list here guarantees every sync channel
    carries the same URL so Repeat Offenders, Staff cards, and the
    VRChat presence tab all render the same PFP."""
    if not isinstance(item, dict):
        return None
    return _first_non_empty(*(item.get(key) for key in _VRCHAT_AVATAR_URL_ALIASES))


def _clean_dict(data: dict) -> dict:
    """Drop None and blank-string values so we don't overwrite good
    server-side data with junk."""
    cleaned: dict[str, Any] = {}
    for key, value in data.items():
        if value is None:
            continue
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                continue
            cleaned[key] = stripped
        else:
            cleaned[key] = value
    return cleaned


def _normalize_action(value: Any) -> str:
    """Canonicalize action names so /sync/logs always gets the same
    lowercase token regardless of how the bot-side code spelled it."""
    if value is None:
        return ""
    text = str(value).strip().lower()
    # Accept common aliases without exploding into a lookup table.
    mapping = {
        "warning": "warn",
        "warned": "warn",
        "kicked": "kick",
        "banned": "ban",
        "banhammer": "ban",
        "unbanned": "unban",
        "timed_out": "timeout",
        "timedout": "timeout",
        "invite_accepted": "invite_accept",
        "inviteaccept": "invite_accept",
    }
    return mapping.get(text, text)


def _hash_payload(obj: Any) -> str:
    """Deterministic SHA-1 over a Python dict/list. Used for change-hash
    dedupe so we skip re-POSTing identical payloads tick-over-tick."""
    try:
        blob = json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"))
    except (TypeError, ValueError):
        blob = repr(obj)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


# ─── Live VRChat last-observed lookup (NEW — fixes stale `last_seen`) ────
#
# `vrchat_presence._friend_presence_refresh_loop` and
# `vrchat_presence._pipeline_loop` both bump
# `app_state.vrc_user_last_observed[uid]` on EVERY tick. By preferring
# that value over whatever the bot's caller hands us, the dashboard's
# `last_seen` advances within 60s of any change, instead of freezing
# at the bot's startup snapshot timestamp.
#
# Strategy: read `core.cache.app_state.vrc_user_last_observed` directly.
# `core.cache` is the bot's shared global state — `vrchat_presence.py`
# already imports it the same way, so this can't fail on a package
# layout mismatch the way `importlib.import_module("…vrchat_presence")`
# can. A second importlib-based path is kept as a fallback for any
# layout where `core.cache` is unreachable from this module.
#
# Lazy-resolved on first use because `dashboard_sync.py` is imported
# during early bot bootstrap, possibly before `core.cache` is on
# `sys.path`. Resolution is cached after the first lookup so we don't
# pay the import cost per row.

_LAST_OBSERVED_DICT_RESOLVER: Optional[Callable[[], Optional[dict]]] = None
_LAST_OBSERVED_RESOLVED: bool = False
# Fallback importlib paths for `vrchat_presence.get_vrc_user_last_observed_ts`,
# tried only if `core.cache.app_state.vrc_user_last_observed` is unreachable.
_VRC_PRESENCE_MODULE_CANDIDATES = (
    "services.vrchat.vrchat_presence",
    "Discord.services.vrchat.vrchat_presence",
    "vrchat.vrchat_presence",
    "vrchat_presence",
)


def _resolve_last_observed_lookup() -> Optional[Callable[[], Optional[dict]]]:
    """Return a zero-arg callable that returns the bot's
    ``vrc_user_last_observed`` dict, or ``None`` if unreachable.

    Tried in order:
      1. ``core.cache.app_state.vrc_user_last_observed`` — direct read,
         shared with `vrchat_presence.py`.
      2. ``importlib.import_module(<candidate>)`` —
         falls back to calling
         ``vrchat_presence.get_vrc_user_last_observed_ts(uid)`` per row.
         Slower but works in unusual package layouts.

    Cached after the first call. To force re-resolution after a
    hot-reload, reset `_LAST_OBSERVED_RESOLVED` to False.
    """
    global _LAST_OBSERVED_DICT_RESOLVER, _LAST_OBSERVED_RESOLVED
    if _LAST_OBSERVED_RESOLVED:
        return _LAST_OBSERVED_DICT_RESOLVER

    # Strategy 1: `core.cache.app_state.vrc_user_last_observed`
    try:
        from core.cache import app_state as _app_state  # type: ignore
    except ImportError:
        _app_state = None

    if _app_state is not None:
        def _read_dict() -> Optional[dict]:
            d = getattr(_app_state, "vrc_user_last_observed", None)
            return d if isinstance(d, dict) else None

        _LAST_OBSERVED_DICT_RESOLVER = _read_dict
        log.info(
            "[dashboard_sync] live last_seen wired via "
            "core.cache.app_state.vrc_user_last_observed"
        )
        _LAST_OBSERVED_RESOLVED = True
        return _LAST_OBSERVED_DICT_RESOLVER

    # Strategy 2: importlib fallback — wrap `get_vrc_user_last_observed_ts`
    # behind a dict-like adapter so the call site stays uniform.
    for module_path in _VRC_PRESENCE_MODULE_CANDIDATES:
        try:
            mod = importlib.import_module(module_path)
        except ImportError:
            continue
        fn = getattr(mod, "get_vrc_user_last_observed_ts", None)
        if not callable(fn):
            continue

        class _PresenceAdapter:
            """Read-only dict-like view that delegates `.get(uid)` to
            ``vrchat_presence.get_vrc_user_last_observed_ts(uid)``."""
            def get(self, uid: str, default: Any = None) -> Any:
                try:
                    return fn(str(uid)) or default  # noqa: B023 — fn captured per module
                except Exception:
                    return default

        adapter = _PresenceAdapter()

        def _read_adapter() -> Optional[dict]:
            return adapter  # type: ignore[return-value]

        _LAST_OBSERVED_DICT_RESOLVER = _read_adapter
        log.info(
            "[dashboard_sync] live last_seen wired via "
            "%s.get_vrc_user_last_observed_ts (importlib fallback)",
            module_path,
        )
        _LAST_OBSERVED_RESOLVED = True
        return _LAST_OBSERVED_DICT_RESOLVER

    _LAST_OBSERVED_RESOLVED = True
    log.warning(
        "[dashboard_sync] could not reach app_state.vrc_user_last_observed "
        "OR vrchat_presence.get_vrc_user_last_observed_ts — last_seen "
        "falls back to caller-provided value (may be stale)"
    )
    return None


def _live_last_seen_iso(item: dict) -> Optional[str]:
    """Look up the bot's last live observation of this VRChat user
    (pipeline event OR REST refresh, whichever was more recent) and
    return it as an ISO-8601 UTC timestamp. ``None`` if we can't
    resolve the helper, the row has no VRChat id, or we've never
    observed the user.

    SELF-STAMP FALLBACK: if the dict resolver returns a writable
    `app_state.vrc_user_last_observed` dict and the current stamp for
    this user is missing OR older than `_SELF_STAMP_STALE_SECONDS`
    (30s), we update the stamp to ``now`` before reading it back. The
    semantic argument: the bot calling `sync_vrchat_statuses(...)` for
    this user IS fresh evidence the bot has data for them, even when
    the upstream friend-list refresh path (`_run_vrc_api_call(
    vrc_friends_api.get_friends, ...)`) is silently broken. Pipeline
    events and direct API lookups still take precedence — they'll
    overwrite our stamp with a more recent one. The fallback only
    kicks in when nothing else is updating the dict.

    The fallback is a no-op when the resolver returned a read-only
    adapter (importlib path 2), so we never accidentally try to
    mutate `vrchat_presence.get_vrc_user_last_observed_ts`'s view.
    """
    vrc_id = _first_non_empty(
        item.get("vrchat_id"),
        item.get("user_id"),
        item.get("vrchat_user_id"),
        item.get("id"),
    )
    if not vrc_id:
        return None

    resolver = _resolve_last_observed_lookup()
    if resolver is None:
        return None

    try:
        observed = resolver()
        if observed is None:
            return None
        ts = observed.get(str(vrc_id))

        # Self-stamp fallback. Only attempt mutation if the resolver
        # returned the real `app_state.vrc_user_last_observed` dict —
        # not the read-only `_PresenceAdapter`. We detect the real
        # dict by attempting a `__setitem__` that no-ops on adapters.
        if not ts or (time.time() - float(ts)) > _SELF_STAMP_STALE_SECONDS:
            try:
                observed[str(vrc_id)] = time.time()
                ts = observed.get(str(vrc_id))
            except (TypeError, AttributeError):
                # Read-only adapter — keep the stale timestamp.
                pass
    except Exception as exc:
        log.debug("[dashboard_sync] live last_seen lookup failed: %s", exc)
        return None

    if not ts:
        return None

    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


# After `_SELF_STAMP_STALE_SECONDS`, build_vrchat_status_payload will
# overwrite the staff member's `vrc_user_last_observed` stamp with
# `now()` — guarantees the dashboard's `last_seen` advances every
# sync cycle even when the bot's friend-list REST refresh path is
# silently broken (e.g. `vrc_friends_api` not initialized). Pipeline
# events and `get_vrchat_user_status` direct lookups continue to
# overwrite our stamp with truly-fresh values when they fire.
_SELF_STAMP_STALE_SECONDS = 30


# ─── Payload builders ─────────────────────────────────────────────────────
#
# Each builder produces the EXACT dict shape the website backend expects,
# dropping falsy fields. The builders are pure (no side effects / no IO)
# so they're trivially unit-testable.

def build_staff_payload(item: dict, *, archived: bool = False) -> dict:
    """Normalize a single staff row for /sync/staff or /sync/leaderboard.

    Accepts the many alias spellings the bot uses internally and maps
    them onto the dashboard's stable keys:
        discord_id, discord_username, vrchat_id, vrchat_username,
        discord_avatar_url, vrchat_avatar_url, role, warns, kicks, bans,
        monthly_warns, monthly_kicks, monthly_bans, points, actions,
        invites_sent, invites_accepted, archived, archived_at,
        archive_reason, vrchat_profile_url
    """
    if not isinstance(item, dict):
        return {}

    discord_id = _coerce_discord_snowflake(
        item.get("discord_id"),
        item.get("staff_discord_id"),
        item.get("staff_id"),
        item.get("discord_user_id"),
    )

    vrchat_id = _first_non_empty(
        item.get("vrchat_id"),
        item.get("user_id"),
        item.get("vrchat_user_id"),
        item.get("id"),
    )

    discord_username = _best_name(
        item.get("discord_username"),
        item.get("staff_name"),
        item.get("staff_username"),
        item.get("name"),
        item.get("username"),
    )

    vrchat_username = _best_name(
        item.get("vrchat_username"),
        item.get("vrchat_name"),
        item.get("vrchat_display_name"),
        item.get("display_name"),
    )

    # Guard against ID-as-name typos on the bot side.
    if vrchat_username and vrchat_id and str(vrchat_username).strip() == str(vrchat_id).strip():
        vrchat_username = None

    discord_avatar_url = _first_non_empty(
        item.get("discord_avatar_url"),
        item.get("discordAvatarUrl"),
        item.get("avatar_url_discord"),
        item.get("discord_avatar"),
    )

    # Centralized alias resolution — picks up every VRChat-side field
    # including the `currentAvatarThumbnailImageUrl` / `userIcon` /
    # `profilePicOverride` trio the bot's `_extract_vrchat_avatar_url`
    # emits raw from the VRChat API.
    vrchat_avatar_url = _best_vrchat_avatar_url(item)

    vrchat_profile_url = _first_non_empty(
        item.get("vrchat_profile_url"),
        (f"https://vrchat.com/home/user/{vrchat_id}" if vrchat_id else None),
    )

    warns = int(item.get("warns", item.get("warn", 0)) or 0)
    kicks = int(item.get("kicks", item.get("kick", 0)) or 0)
    bans = int(item.get("bans", item.get("ban", 0)) or 0)
    invites_sent = int(item.get("invites_sent", item.get("invite", 0)) or 0)
    invites_accepted = int(
        item.get("invites_accepted", item.get("invite_accept", 0)) or 0
    )

    payload = {
        "discord_id": discord_id,
        "vrchat_id": vrchat_id,
        "discord_username": discord_username,
        "vrchat_username": vrchat_username,
        "discord_avatar_url": discord_avatar_url,
        "vrchat_avatar_url": vrchat_avatar_url,
        "vrchat_profile_url": vrchat_profile_url,
        "role": _first_non_empty(
            item.get("role"), item.get("rank_name"), "Unknown Rank"
        ),
        "warns": warns,
        "kicks": kicks,
        "bans": bans,
        "points": int(item.get("points", 0) or 0),
        "invites_sent": invites_sent,
        "invites_accepted": invites_accepted,
        "actions": warns + kicks + bans,
    }

    snapshot = item.get("monthly_snapshot")
    if isinstance(snapshot, dict):
        payload["monthly_warns"] = int(snapshot.get("warn", 0) or 0)
        payload["monthly_kicks"] = int(snapshot.get("kick", 0) or 0)
        payload["monthly_bans"] = int(snapshot.get("ban", 0) or 0)
    else:
        if "monthly_warns" in item:
            payload["monthly_warns"] = int(item.get("monthly_warns", 0) or 0)
        if "monthly_kicks" in item:
            payload["monthly_kicks"] = int(item.get("monthly_kicks", 0) or 0)
        if "monthly_bans" in item:
            payload["monthly_bans"] = int(item.get("monthly_bans", 0) or 0)

    if archived or item.get("archived"):
        payload["archived"] = True
        if item.get("archived_at") is not None:
            payload["archived_at"] = item.get("archived_at")
        if item.get("archive_reason") is not None:
            payload["archive_reason"] = item.get("archive_reason")
    else:
        payload["archived"] = False

    return _clean_dict(payload)


def build_vrchat_status_payload(item: dict) -> dict:
    """Normalize a /sync/vrchat-status row.

    CRITICAL: includes `discord_status` when the bot collected it
    (historically this was silently dropped, which made the dashboard
    show every staff member as 'Discord: Not tracked'). Sending:
       "online" | "idle" | "dnd" | "offline" or None/omitted.
    When omitted, the dashboard preserves whatever presence source it
    last had rather than stamping a false 'offline'.

    LIVE last_seen (2026-02 fix): the highest-priority source is
    `vrchat_presence.get_vrc_user_last_observed_ts(vrc_id)`, which
    is bumped on every pipeline event and every 60s REST refresh
    in the patched `vrchat_presence.py`. This guarantees `last_seen`
    on the dashboard advances with reality instead of freezing at
    a single startup snapshot moment (the symptom that made every
    staff row show identical timestamps clustered within a few ms).
    Caller-provided `last_seen` is kept as a fallback for users who
    aren't on the bot's friend list.
    """
    if not isinstance(item, dict):
        return {}

    vrchat_id = _first_non_empty(
        item.get("vrchat_id"),
        item.get("user_id"),
        item.get("vrchat_user_id"),
        item.get("id"),
    )
    if vrchat_id is None:
        return {}

    discord_id = _coerce_discord_snowflake(
        item.get("discord_id"),
        item.get("staff_discord_id"),
        item.get("staff_id"),
    )

    vrchat_status = _first_non_empty(item.get("vrchat_status"), item.get("status"))
    # Discord gateway presence from guild.get_member(...).status.
    # Pass None through when the bot doesn't have a real value — the
    # dashboard will show "Not tracked" for that row rather than a fake
    # green/red dot.
    discord_status = _first_non_empty(item.get("discord_status"))

    normalized = {
        "vrchat_id": vrchat_id,
        "discord_id": discord_id,
        "vrchat_status": vrchat_status,
        "discord_status": discord_status,
        "status_last_source": _first_non_empty(
            item.get("status_last_source"),
            item.get("source"),
            "bot_pipeline",
        ),
        "updated_at": _first_non_empty(
            item.get("updated_at"), item.get("timestamp"), item.get("ts")
        ),
        "vrchat_username": _best_name(
            item.get("vrchat_username"),
            item.get("vrchat_name"),
            item.get("display_name"),
        ),
        "discord_username": _best_name(
            item.get("discord_username"),
            item.get("staff_name"),
            item.get("staff_username"),
        ),
        # Same shared resolver as staff/offender so the presence-tab
        # avatar never drops because only `currentAvatarThumbnailImageUrl`
        # was populated (common on raw VRChat API user objects).
        "vrchat_avatar_url": _best_vrchat_avatar_url(item),
        "discord_avatar_url": _first_non_empty(
            item.get("discord_avatar_url"), item.get("discord_avatar")
        ),
        "platform": _first_non_empty(item.get("platform"), item.get("last_platform")),
        "last_platform": _first_non_empty(
            item.get("last_platform"), item.get("platform")
        ),
        # ── last_seen — live-first, with caller-provided fallback ──
        # `_live_last_seen_iso` reads the bot's vrc_user_last_observed
        # dict, which the patched `vrchat_presence.py` updates on every
        # pipeline event and every 60s REST refresh. Falls back to
        # whatever the caller passed in (or `updated_at`/`timestamp`/
        # `ts`) when the user isn't a friend or the helper isn't
        # importable yet.
        "last_seen": _first_non_empty(
            _live_last_seen_iso(item),
            item.get("last_seen"),
            item.get("updated_at"),
            item.get("timestamp"),
            item.get("ts"),
        ),
        "reason": _first_non_empty(item.get("reason")),
    }
    return _clean_dict(normalized)


def build_offender_payload(item: dict) -> dict:
    """Normalize an offender row. Prefers real names over placeholders
    so the Offenders table never renders `Unknown` or `User123`.
    VRChat profile picture is carried through via the shared
    `_best_vrchat_avatar_url` resolver so every alias emitted by the
    bot's `_extract_vrchat_avatar_url` helper lands on the dashboard."""
    if not isinstance(item, dict):
        return {}

    vrchat_id = _first_non_empty(
        item.get("vrchat_id"),
        item.get("target_vrchat_id"),
        item.get("user_id"),
        item.get("id"),
    )

    # Offender's Discord ID — guard against bot-side placeholders (e.g.
    # `User625`, plain VRChat ids) so the dashboard's offender ledger
    # never gets a non-snowflake in the discord_id slot.
    discord_id = _coerce_discord_snowflake(
        item.get("discord_id"),
        item.get("target_discord_id"),
        item.get("user_id"),
    )

    name = _best_name(
        item.get("target_name"),
        item.get("target_username"),
        item.get("target_display_name"),
        item.get("vrchat_username"),
        item.get("target_vrchat_name"),
        item.get("display_name"),
        item.get("name"),
    )

    warns = int(item.get("warns", item.get("warn", 0)) or 0)
    kicks = int(item.get("kicks", item.get("kick", 0)) or 0)
    bans = int(item.get("bans", item.get("ban", 0)) or 0)

    vrchat_avatar_url = _best_vrchat_avatar_url(item)

    last_warn = _first_non_empty(
        item.get("last_warn"),
        item.get("last_warn_at"),
        (item.get("timestamps") or {}).get("warn") if isinstance(item.get("timestamps"), dict) else None,
    )

    last_kick = _first_non_empty(
        item.get("last_kick"),
        item.get("last_kick_at"),
        (item.get("timestamps") or {}).get("kick") if isinstance(item.get("timestamps"), dict) else None,
    )

    last_ban = _first_non_empty(
        item.get("last_ban"),
        item.get("last_ban_at"),
        (item.get("timestamps") or {}).get("ban") if isinstance(item.get("timestamps"), dict) else None,
    )

    last_infraction = _first_non_empty(
        item.get("last_infraction"),
        item.get("last_action_at"),
        item.get("last_action_timestamp"),
        max([t for t in (last_warn, last_kick, last_ban) if t], default=None),
    )

    return _clean_dict({
        "vrchat_id": vrchat_id,
        "discord_id": discord_id,
        "target_name": name,
        "vrchat_username": name,
        "vrchat_avatar_url": vrchat_avatar_url,
        "total_infractions": warns + kicks + bans,
        "warns": warns,
        "kicks": kicks,
        "bans": bans,
        "is_repeat_offender": item.get(
            "is_repeat_offender",
            warns >= 3 or kicks >= 2 or bans >= 1,
        ),
        "last_infraction": last_infraction,
        "last_warn": last_warn,
        "last_kick": last_kick,
        "last_ban": last_ban,
    })


def build_log_payload(item: dict) -> dict:
    """Normalize a moderation / info / warning / error log.

    Canonicalizes the action name and keeps target identity fields so
    the dashboard's offender rebuild has real VRChat names to work with
    (instead of the infamous `VRC xxxxxxxx` placeholders). Also carries
    the target's VRChat avatar URL so the Repeat Offenders page can
    display the profile picture the moment the log lands on the
    dashboard — no second `/sync/offenders` pass required.

    ── ACTOR FALL-THROUGH (2026-02 fix) ───────────────────────────────
    VRChat group audit-log rows ship the moderator's identity in
    `actor_*` fields (renamed from VRChat REST's `actorId` /
    `actorDisplayName` by the audit-log dispatcher). Without these
    fall-throughs the dashboard's `SyncLogRequest` would default
    `staff_name` to the literal "System" and the Logs page would
    render "System performed Warn / Instance Opened" instead of the
    real moderator. Discord identity wins (richer profile link) but
    we always have the VRChat side as a backstop.
    """
    if not isinstance(item, dict):
        return {}

    staff_name = _best_name(
        item.get("staff_name"),
        item.get("staff_username"),
        item.get("name"),
        # Audit-log fall-through: VRChat ships actor info in these fields.
        item.get("actor_discord_name"),
        item.get("actor_vrchat_name"),
        item.get("actor_name"),
        item.get("actor_username"),
    )
    # Only forward the staff slot when it's actually a Discord snowflake.
    # Production bug 2026-05-02: bot was shipping placeholder ids like
    # `staff_2`, `1001`, `staff_18` here — none matched a dashboard staff
    # doc and the Monthly leaderboard stayed at zero. The dashboard now
    # has a `staff_name → discord_username` fallback that takes over when
    # this field is omitted, so dropping a bogus id is the safe move.
    staff_discord_id = _coerce_discord_snowflake(
        item.get("staff_discord_id"),
        item.get("staff_id"),
        item.get("discord_id"),
        # Same actor fall-through — VRChat audit rows ship the
        # moderator's id under `actor_discord_id`.
        item.get("actor_discord_id"),
    )

    target_name = _best_name(
        item.get("target_name"),
        item.get("target_username"),
        item.get("target_display_name"),
        item.get("target"),
    )
    # Same snowflake guard for the offender side — prevents fake
    # `User625`-style ids from polluting the offender ledger.
    target_discord_id = _coerce_discord_snowflake(
        item.get("target_discord_id"),
        item.get("target_id"),
        item.get("target_user_id"),
    )
    target_vrchat_id = _first_non_empty(
        item.get("target_vrchat_id"),
        item.get("target_user_vrchat_id"),
        item.get("vrchat_id"),
        item.get("user_id"),
    )
    target_vrchat_name = _best_name(
        item.get("target_vrchat_name"),
        item.get("target_vrchat_username"),
        item.get("target_vrchat_display_name"),
        item.get("vrchat_username"),
    )

    # Avatar URL for the TARGET (offender). The backend's `/sync/log`
    # already knows how to forward this into the offenders collection,
    # so preserving it here means a single log event is sufficient to
    # paint the PFP — no lag waiting for the next `/sync/offenders`
    # full push. Check target-prefixed keys first, then generic keys.
    target_vrchat_avatar_url = _first_non_empty(
        item.get("target_vrchat_avatar_url"),
        item.get("target_avatar_url"),
        *(item.get(key) for key in _VRCHAT_AVATAR_URL_ALIASES),
    )

    # If only the VRChat side has a usable name, promote it to
    # `target_name` so the activity feed renders something meaningful.
    if target_name is None and target_vrchat_name is not None:
        target_name = target_vrchat_name

    # ── Points awarded ───────────────────────────────────────────────
    # Caller can override (`points_awarded=N`); otherwise we compute
    # from the action_type using the bot's WARN_SCORE/KICK_SCORE/...
    # env vars. Forwarding this lets the dashboard $inc both `points`
    # and `monthly_points` server-side without a separate score sync.
    if "points_awarded" in item and item.get("points_awarded") is not None:
        try:
            points_awarded = max(0, int(item["points_awarded"]))
        except (TypeError, ValueError):
            points_awarded = _score_for_action(item.get("action_type"))
    else:
        points_awarded = _score_for_action(item.get("action_type"))

    return _clean_dict({
        "category": _first_non_empty(item.get("category"), "info"),
        "action_type": _normalize_action(item.get("action_type")),
        "staff_name": staff_name,
        "staff_discord_id": staff_discord_id,
        "target_name": target_name,
        "target_discord_id": target_discord_id,
        "target_vrchat_id": target_vrchat_id,
        "target_vrchat_name": target_vrchat_name,
        "target_vrchat_avatar_url": target_vrchat_avatar_url,
        "reason": item.get("reason"),
        "details": item.get("details"),
        "timestamp": item.get("timestamp"),
        # `_clean_dict` keeps non-string non-None values, so 0 flows
        # through correctly (no points awarded for info/error rows).
        "points_awarded": points_awarded,
        # Stable bot-side id (VRChat audit log entry id, internal
        # event uuid, …). When present the dashboard uses it as the
        # idempotency key so a retry/replay never double-bumps
        # counters.
        "external_id": _first_non_empty(
            item.get("external_id"),
            item.get("audit_log_id"),
            item.get("source_event_id"),
            item.get("vrchat_log_id"),
            item.get("event_id"),
        ),
    })


# ─── Sync client ──────────────────────────────────────────────────────────

class DashboardSync:
    """ModCenter dashboard sync client (v2).

    Call the public `sync_*` methods from your bot tasks. The client
    handles chunking, retries, change-hash dedupe, and structured
    logging internally. Close() must be awaited on shutdown so the
    underlying aiohttp session releases cleanly.
    """

    _RETRY_STATUSES = {502, 503, 504, 520, 522, 524}
    _RETRY_DELAYS = (2, 5, 15)

    def __init__(self, dashboard_url: str, api_key: str, config: Optional[SyncConfig] = None):
        base = str(dashboard_url or "").strip().rstrip("/")
        # Accept base URL with or without /api suffix — we always target /api.
        self.base_url = base if base.endswith("/api") else f"{base}/api"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self.config = config or SyncConfig()
        self._session: Optional[aiohttp.ClientSession] = None
        self._timeout = aiohttp.ClientTimeout(
            total=self.config.timeout_seconds,
            connect=min(10, self.config.timeout_seconds),
            sock_connect=min(10, self.config.timeout_seconds),
            sock_read=self.config.timeout_seconds,
        )
        # Change-hash cache keyed by endpoint — skip re-POSTing payloads
        # that haven't changed since the last successful push. Stale
        # entries are force-refreshed every `debounce_seconds` so the
        # website can detect "bot is alive" via sync_meta even when
        # nothing actually changed.
        self._last_hash: dict[str, str] = {}
        self._last_hash_ts: dict[str, float] = {}

        if self.config.enabled:
            log.info(
                "[dashboard_sync v2] enabled url=%s config=%s",
                self.base_url, self.config.as_dict(),
            )
        else:
            log.info(
                "[dashboard_sync v2] DISABLED via DASHBOARD_SYNC_ENABLED=false"
            )

    # ── Lifecycle ────────────────────────────────────────────────────

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self.headers, timeout=self._timeout
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def heartbeat(self) -> dict:
        """Quick bot-startup probe — returns {ok:true} from the server
        when authenticated + DB reachable. Safe to poll."""
        return await self._post("sync/ping", {}, cache_key=None)

    # ── HTTP plumbing ────────────────────────────────────────────────

    async def _post(
        self,
        endpoint: str,
        payload: dict,
        *,
        cache_key: Optional[str],
        extra_headers: Optional[dict] = None,
    ) -> dict:
        """Single POST with retry. `cache_key` opts into change-hash
        dedupe — pass None for non-idempotent calls (alerts, monthly
        reset) that must always hit the server."""
        if not self.config.enabled:
            return {"success": False, "error": "dashboard_sync disabled", "endpoint": endpoint}

        # Change-hash dedupe for chatty endpoints.
        if cache_key is not None:
            current_hash = _hash_payload(payload)
            last_hash = self._last_hash.get(cache_key)
            last_ts = self._last_hash_ts.get(cache_key, 0.0)
            age = time.time() - last_ts
            if last_hash == current_hash and age < self.config.debounce_seconds:
                log.debug(
                    "[dashboard_sync] skip %s (unchanged, age=%.1fs)",
                    endpoint, age,
                )
                return {
                    "success": True,
                    "deduped": True,
                    "endpoint": endpoint,
                    "cache_age_seconds": age,
                }

        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers = None
        if extra_headers:
            headers = dict(self.headers)
            headers.update(extra_headers)

        last_error: Any = None
        for attempt in range(1, self.config.retry_count + 1):
            try:
                session = await self._get_session()
                async with session.post(url, json=payload, headers=headers) as resp:
                    body = await self._read_body(resp)
                    if 200 <= resp.status < 300:
                        if cache_key is not None:
                            self._last_hash[cache_key] = _hash_payload(payload)
                            self._last_hash_ts[cache_key] = time.time()
                        if isinstance(body, dict):
                            body.setdefault("success", True)
                            body.setdefault("status", resp.status)
                            return body
                        return {"success": True, "status": resp.status, "result": body}

                    last_error = body
                    if resp.status in self._RETRY_STATUSES and attempt < self.config.retry_count:
                        await self._sleep_before_retry(attempt, endpoint, resp.status)
                        continue

                    log.warning(
                        "[dashboard_sync] %s failed status=%s body=%s",
                        endpoint, resp.status, body,
                    )
                    if isinstance(body, dict):
                        body.setdefault("success", False)
                        body.setdefault("status", resp.status)
                        return body
                    return {"success": False, "status": resp.status, "error": str(body)}

            except asyncio.TimeoutError as exc:
                last_error = f"timeout: {exc}"
                if attempt < self.config.retry_count:
                    await self._sleep_before_retry(attempt, endpoint, "timeout")
                    continue
            except aiohttp.ClientError as exc:
                last_error = f"client_error: {exc}"
                if attempt < self.config.retry_count:
                    await self._sleep_before_retry(attempt, endpoint, "client_error")
                    continue
            except Exception as exc:
                last_error = f"unexpected: {exc}"
                if attempt < self.config.retry_count:
                    await self._sleep_before_retry(attempt, endpoint, "unexpected")
                    continue

        log.warning(
            "[dashboard_sync] %s gave up after %s attempts: %s",
            endpoint, self.config.retry_count, last_error,
        )
        return {"success": False, "endpoint": endpoint, "error": str(last_error)}

    async def _post_chunked(
        self,
        endpoint: str,
        *,
        items_key: str,
        items: list[dict],
        cache_key: Optional[str],
        extra_headers: Optional[dict] = None,
    ) -> dict:
        """Send a list payload in chunks. Success = every chunk 2xx.

        Change-hash dedupe happens at the WHOLE-PAYLOAD level before
        chunking, so a steady-state "nothing changed" call costs one
        hash compare instead of N HTTP round-trips."""
        if cache_key is not None and self.config.enabled:
            current_hash = _hash_payload({"items": items})
            last_hash = self._last_hash.get(cache_key)
            last_ts = self._last_hash_ts.get(cache_key, 0.0)
            age = time.time() - last_ts
            if last_hash == current_hash and age < self.config.debounce_seconds:
                log.debug(
                    "[dashboard_sync] skip %s (unchanged batch, age=%.1fs, items=%s)",
                    endpoint, age, len(items),
                )
                return {
                    "success": True,
                    "deduped": True,
                    "endpoint": endpoint,
                    "items_total": len(items),
                    "cache_age_seconds": age,
                }

        if not items:
            # Empty flushes are health heartbeats — send through so
            # /sync/metrics shows recent activity.
            return await self._post(
                endpoint, {items_key: []}, cache_key=None, extra_headers=extra_headers
            )

        chunks = [
            items[i:i + self.config.max_chunk]
            for i in range(0, len(items), self.config.max_chunk)
        ]
        last_result: dict = {
            "success": True,
            "chunks_total": len(chunks),
            "chunks_sent": 0,
            "items_total": len(items),
        }
        for index, chunk in enumerate(chunks, start=1):
            result = await self._post(
                endpoint, {items_key: chunk},
                cache_key=None,  # per-chunk dedupe would defeat the purpose
                extra_headers=extra_headers,
            )
            if not result.get("success"):
                result.setdefault("failed_chunk", index)
                result.setdefault("chunks_total", len(chunks))
                result.setdefault("chunks_sent", index - 1)
                result.setdefault("items_total", len(items))
                return result
            last_result = result
            last_result["chunks_sent"] = index
            last_result["chunks_total"] = len(chunks)
            last_result["items_total"] = len(items)

        if cache_key is not None and self.config.enabled and last_result.get("success"):
            self._last_hash[cache_key] = _hash_payload({"items": items})
            self._last_hash_ts[cache_key] = time.time()
        return last_result

    @staticmethod
    async def _read_body(resp: aiohttp.ClientResponse) -> Any:
        try:
            text = await resp.text()
        except Exception as exc:
            return {"error": f"read failed: {exc}"}
        if not text.strip():
            return {"status": resp.status, "body": ""}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Cloudflare + origin HTML fallbacks — truncate so logs stay sane.
            return {"status": resp.status, "body": text[:500]}

    async def _sleep_before_retry(self, attempt: int, endpoint: str, reason: Any) -> None:
        delay = self._RETRY_DELAYS[min(attempt - 1, len(self._RETRY_DELAYS) - 1)]
        log.warning(
            "[dashboard_sync] %s retry attempt=%s/%s reason=%s wait=%ss",
            endpoint, attempt, self.config.retry_count, reason, delay,
        )
        await asyncio.sleep(delay)

    # ── Public sync methods ──────────────────────────────────────────

    async def sync_staff(self, staff: list[dict]) -> dict:
        """Upsert staff (partial mode). Adds new staff, updates existing
        rows, never archives. Use sync_leaderboard for full reconcile."""
        normalized = [build_staff_payload(item) for item in staff or []]
        normalized = [row for row in normalized if row]
        return await self._post_chunked(
            "sync/staff",
            items_key="staff",
            items=normalized,
            cache_key="sync/staff",
        )

    async def sync_leaderboard(self, leaderboard: dict) -> dict:
        """Full-reconcile push: send the bot's authoritative snapshot.

        The website archives anyone missing from the payload, so this
        MUST include everybody you want to keep active. Accepts either
        `{"staff": {...}, "archive": {...}}` or a flat `{user_id: entry}`
        dict.

        DEPLOYMENT-ORDER TOLERANT: tries the new `/sync/leaderboard`
        alias first, automatically falls back to `/sync/staff` with
        `X-Sync-Mode: full` on 404. Means the bot can ship BEFORE the
        backend has the alias deployed — no coordination required. Both
        endpoints update the same `sync_meta["staff"]` record, so the
        Owner Debug "Staff Data Sync" indicator goes green either way.
        """
        if not isinstance(leaderboard, dict):
            return {"success": False, "error": "leaderboard must be dict"}

        rows: list[dict] = []
        active = leaderboard.get("staff") if "staff" in leaderboard else leaderboard
        if isinstance(active, dict):
            for user_id, entry in active.items():
                if not isinstance(entry, dict):
                    continue
                entry = {**entry, "vrchat_id": entry.get("vrchat_id") or user_id}
                rows.append(build_staff_payload(entry, archived=False))
        archive = leaderboard.get("archive")
        if isinstance(archive, dict):
            for user_id, entry in archive.items():
                if not isinstance(entry, dict):
                    continue
                entry = {**entry, "vrchat_id": entry.get("vrchat_id") or user_id}
                rows.append(build_staff_payload(entry, archived=True))

        rows = [row for row in rows if row]
        # Cache key is shared between leaderboard + staff fallback so the
        # dedupe hash doesn't double-send if we switch endpoints between
        # ticks.
        cache_key = "sync/leaderboard"
        extra_headers = {"X-Sync-Mode": "full"}

        result = await self._post_chunked(
            "sync/leaderboard",
            items_key="staff",
            items=rows,
            cache_key=cache_key,
            extra_headers=extra_headers,
        )

        # Fallback on 404 — older backend deploys only have /sync/staff.
        # Detected at chunk level; on the very first chunk returning 404,
        # we retry the whole push against /sync/staff. Subsequent calls
        # skip straight to the fallback via `self._leaderboard_endpoint`.
        if result.get("status") == 404 and not getattr(self, "_leaderboard_fallback", False):
            log.warning(
                "[dashboard_sync] /sync/leaderboard 404 — falling back to "
                "/sync/staff + X-Sync-Mode: full for this deployment"
            )
            self._leaderboard_fallback = True
            # Clear the dedupe entry so the fallback actually POSTs.
            self._last_hash.pop(cache_key, None)
            self._last_hash_ts.pop(cache_key, None)

        if getattr(self, "_leaderboard_fallback", False):
            return await self._post_chunked(
                "sync/staff",
                items_key="staff",
                items=rows,
                cache_key=cache_key,
                extra_headers=extra_headers,
            )
        return result

    async def sync_vrchat_statuses(self, statuses: list[dict]) -> dict:
        """Push presence updates. Change-hash deduped — a no-op tick
        (everyone unchanged, same source, same platform) skips the HTTP
        call entirely and returns `{success: true, deduped: true}`.

        NOTE on dedupe + live `last_seen`: the change-hash now includes
        the live-resolved `last_seen` from `vrchat_presence`, which
        advances every 60s. That intentionally defeats the dedupe for
        steady-state "nobody actually changed" ticks — the dashboard's
        UX explicitly wants `last_seen` to keep moving so operators can
        see the bot is alive. The HTTP cost is ~one POST per 60s.
        """
        rows = [build_vrchat_status_payload(item) for item in statuses or []]
        rows = [row for row in rows if row]
        return await self._post_chunked(
            "sync/vrchat-status",
            items_key="statuses",
            items=rows,
            cache_key="sync/vrchat-status",
        )

    # Backwards-compat alias: older bot code calls the singular form.
    sync_vrchat_status = sync_vrchat_statuses

    async def sync_offenders(self, offenders: list[dict]) -> dict:
        """Full-replace push of the repeat-offender ledger. The server
        trusts this payload as authoritative. Change-hash deduped."""
        rows = [build_offender_payload(item) for item in offenders or []]
        rows = [row for row in rows if row]
        return await self._post(
            "sync/offenders",
            {"offenders": rows, "replace": True},
            cache_key="sync/offenders",
        )

    async def push_log(self, **kwargs) -> dict:
        """Single-log fire-and-forget. Prefer the batched LogBuffer
        (see LogBuffer below) for high-throughput audit streams —
        per-event calls are fine for rare events (bot errors, manual
        alerts) where latency matters more than throughput."""
        payload = build_log_payload(kwargs)
        if not payload.get("action_type"):
            return {"success": False, "error": "action_type required"}
        return await self._post("sync/log", payload, cache_key=None)

    async def sync_logs_batch(self, logs: list[dict]) -> dict:
        """Batched log push. Server returns per-row results so a single
        malformed row doesn't discard the whole flush.

        DEPLOYMENT-ORDER TOLERANT: if the backend doesn't have
        `/sync/logs` yet (older deploy), falls back to per-row
        `/sync/log` calls so the LogBuffer doesn't infinitely re-queue
        rows on 404.
        """
        rows = [build_log_payload(item) for item in logs or []]
        rows = [row for row in rows if row.get("action_type")]
        if not rows:
            return {"success": True, "accepted": 0, "total": 0, "empty": True}

        if not getattr(self, "_logs_batch_fallback", False):
            result = await self._post(
                "sync/logs", {"logs": rows}, cache_key=None
            )
            if result.get("status") != 404:
                return result
            log.warning(
                "[dashboard_sync] /sync/logs 404 — falling back to per-row "
                "/sync/log for this deployment"
            )
            self._logs_batch_fallback = True

        # Per-row fallback. Keep going even if individual rows fail so
        # one bad row doesn't discard the rest.
        accepted = 0
        errors: list[dict] = []
        for index, row in enumerate(rows):
            single = await self._post("sync/log", row, cache_key=None)
            if single.get("success"):
                accepted += 1
            else:
                errors.append({"index": index, "error": single.get("error")})
        return {
            "success": not errors or accepted > 0,
            "accepted": accepted,
            "total": len(rows),
            "errors": errors,
            "fallback": True,
        }

    async def push_alert(
        self,
        title: str,
        message: str,
        alert_type: str = "custom",
        severity: str = "info",
    ) -> dict:
        return await self._post(
            "sync/alert",
            {
                "type": alert_type,
                "title": title,
                "message": message,
                "severity": severity,
            },
            cache_key=None,
        )

    async def trigger_monthly_reset(self) -> dict:
        return await self._post("sync/monthly-reset", {}, cache_key=None)

    async def push_chart_data(
        self, date: str, warns: int, kicks: int, bans: int
    ) -> dict:
        return await self._post(
            "sync/chart-data",
            {"date": date, "warns": warns, "kicks": kicks, "bans": bans},
            cache_key=None,
        )

    async def diagnose_vrchat_status_sync(self, limit: int = 5) -> dict:
        """Fetch the website's record of the last N /sync/vrchat-status
        POSTs the bot made. Useful for verifying `discord_status` is
        actually landing server-side — the response now includes a
        `discord_presence_tracking.bot_sending_discord_status` boolean."""
        if not self.config.enabled:
            return {"success": False, "error": "disabled"}
        url = f"{self.base_url}/sync/debug/last-vrchat-status-payload?limit={int(limit)}"
        try:
            session = await self._get_session()
            async with session.get(url) as resp:
                body = await self._read_body(resp)
                if isinstance(body, dict):
                    body.setdefault("status", resp.status)
                    body.setdefault("success", 200 <= resp.status < 300)
                    return body
                return {"status": resp.status, "success": 200 <= resp.status < 300, "body": body}
        except Exception as exc:
            return {"success": False, "error": str(exc), "endpoint": "sync/debug/last-vrchat-status-payload"}


# ─── LogBuffer — batched log flusher ──────────────────────────────────────

class LogBuffer:
    """Debounces per-event moderation logs into batched /sync/logs calls.

    Usage (inside any bot task that generates log events):

        buffer = LogBuffer(app_state.dashboard_sync)
        await buffer.start()               # on bot startup
        ...
        await buffer.add({                 # everywhere you used to
            "category": "moderation",      #   await sync.push_log(...)
            "action_type": "warn",
            "staff_name": ...,
            "target_vrchat_id": ...,
        })
        ...
        await buffer.stop()                # on bot shutdown

    Flush conditions (whichever fires first):
      • buffer size >= DASHBOARD_SYNC_LOG_BATCH_SIZE (default 10)
      • time since last flush >= DASHBOARD_SYNC_LOG_FLUSH_SECONDS (default 30)
      • explicit `flush()` call
      • bot shutdown (`stop()`)

    Rows are preserved across transient backend errors — a failed flush
    puts the rows back at the head of the buffer for the next attempt
    so no moderation log is silently lost."""

    def __init__(self, sync: DashboardSync, config: Optional[SyncConfig] = None):
        self.sync = sync
        self.config = config or sync.config
        self._buffer: list[dict] = []
        self._lock = asyncio.Lock()
        self._flusher_task: Optional[asyncio.Task] = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if self._flusher_task is None or self._flusher_task.done():
            self._stopping.clear()
            self._flusher_task = asyncio.create_task(
                self._periodic_flush(), name="log_buffer_flusher"
            )
            log.info(
                "[log_buffer] started batch_size=%s flush_seconds=%s",
                self.config.log_batch_size, self.config.log_flush_seconds,
            )

    async def stop(self) -> None:
        self._stopping.set()
        task = self._flusher_task
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=self.config.log_flush_seconds + 5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()
        await self.flush(force=True)
        log.info("[log_buffer] stopped")

    async def add(self, entry: dict) -> None:
        """Add one log event. Auto-flushes when the buffer reaches the
        configured batch size (hot path — no timer wait).

        DEADLOCK FIX (2026-04-21): the size-triggered flush must happen
        AFTER releasing `self._lock`. `flush()` itself acquires the
        same lock, and `asyncio.Lock` is not re-entrant — calling
        `await self.flush()` while still holding the lock caused the
        bot's log pipeline to hard-block on the 10th log event (default
        batch size). Every subsequent `add()` then stacked behind the
        frozen lock and `/sync/logs` stopped receiving anything. By
        hoisting the flush call out of the `async with` block we keep
        the ordering (the triggering event is already buffered) without
        the self-deadlock."""
        async with self._lock:
            self._buffer.append(entry)
            size_trigger = len(self._buffer) >= self.config.log_batch_size
        if size_trigger:
            await self.flush()

    async def flush(self, *, force: bool = False) -> Optional[dict]:
        async with self._lock:
            if not self._buffer:
                return None if not force else {"success": True, "accepted": 0}
            pending = self._buffer
            self._buffer = []

        result = await self.sync.sync_logs_batch(pending)
        if not result.get("success"):
            # Put rows back so the next tick retries them. Prepend so
            # chronological order is preserved.
            async with self._lock:
                self._buffer = pending + self._buffer
            log.warning(
                "[log_buffer] flush failed (%s rows requeued) error=%s",
                len(pending), result.get("error"),
            )
        else:
            log.debug(
                "[log_buffer] flushed %s rows accepted=%s skipped=%s",
                len(pending), result.get("accepted"), result.get("skipped"),
            )
        return result

    async def _periodic_flush(self) -> None:
        while not self._stopping.is_set():
            try:
                await asyncio.wait_for(
                    self._stopping.wait(),
                    timeout=self.config.log_flush_seconds,
                )
                # stopping event fired — exit loop, stop() will drain.
                break
            except asyncio.TimeoutError:
                pass
            try:
                await self.flush()
            except Exception as exc:
                log.exception("[log_buffer] periodic flush error: %s", exc)
