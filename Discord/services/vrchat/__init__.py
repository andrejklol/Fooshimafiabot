import asyncio
import logging
import time

from core.cache import app_state
from core.config import GROUP_ID, VRC_STAFF_ROLE_NAMES
from core.utils import run_blocking, send_error_log, vrchat_cooldown_active

log = logging.getLogger("vrchat_client")

_USER_AGENT = "FooshiMafiaBot/1.3 (contact: fooshimafia@gmail.com)"


# ============================================================
# CONSTANTS
# ============================================================

GROUP_ROLE_REFRESH_TTL_SECONDS = 900
GROUP_MEMBER_REFRESH_TTL_SECONDS = 900
GROUP_INFO_REFRESH_TTL_SECONDS = 900
AMBIGUOUS_STATUS_RECHECK_SECONDS = 6

PIPELINE_URL_BASE = "wss://pipeline.vrchat.cloud/"
PIPELINE_RECONNECT_SECONDS = 5
PIPELINE_STALE_SECONDS = 180

RECENT_ACTIVITY_WINDOW_SECONDS = 20 * 60
FRIEND_PRESENCE_CACHE_SECONDS = 90

VRCHAT_API_RETRIES = 3
VRCHAT_API_BASE_DELAY_SECONDS = 2.0
GROUP_MEMBERS_PAGE_DELAY_SECONDS = 0.5


# ============================================================
# BASIC HELPERS
# ============================================================

def _now_ts() -> float:
    return time.time()


def _normalize_vrc_name(value: str | None) -> str:
    return (value or "").strip().casefold()


def _normalize_vrc_user_id(value: str | None) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None


def _normalize_status_value(value) -> str:
    return str(value or "").strip().lower()


def _extract_first_str_attr(obj, field_names: tuple[str, ...]) -> str | None:
    for field in field_names:
        value = str(getattr(obj, field, None) or "").strip()
        if value:
            return value
    return None


def _extract_role_name(role_obj) -> str | None:
    return _extract_first_str_attr(role_obj, ("name", "display_name", "role_name"))


def _extract_role_id(role_obj) -> str | None:
    return _extract_first_str_attr(role_obj, ("id", "role_id"))


def _extract_member_display_name(member_obj) -> str | None:
    return _extract_first_str_attr(
        member_obj,
        (
            "display_name",
            "displayName",
            "user_display_name",
            "userDisplayName",
            "username",
            "user_name",
            "name",
        ),
    )


def _extract_group_name(group_obj) -> str | None:
    return _extract_first_str_attr(
        group_obj,
        ("name", "display_name", "displayName", "group_name"),
    )


def _extract_group_id(group_obj) -> str | None:
    return _extract_first_str_attr(group_obj, ("id", "group_id", "groupId"))


def _fallback_user_name(user_id: str) -> str:
    return f"User {str(user_id).replace('usr_', '')[:8]}"


def _unique_list(values: list[str]) -> list[str]:
    return list(set(values))


def _member_role_names_from_obj(member_obj) -> list[str]:
    role_names: list[str] = []

    for field in ("roles", "role_names"):
        for value in getattr(member_obj, field, None) or []:
            if hasattr(value, "__dict__"):
                role_name = _extract_role_name(value)
                if role_name:
                    role_names.append(role_name.lower())
            else:
                text = str(value).strip().lower()
                if text:
                    role_names.append(text)

    for role_id in getattr(member_obj, "role_ids", None) or []:
        role_name = app_state.vrc_group_role_map.get(str(role_id))
        if role_name:
            role_names.append(role_name.lower())

    return _unique_list(role_names)


def _member_role_ids_from_obj(member_obj) -> list[str]:
    role_ids: list[str] = []

    for field in ("role_ids", "roleIds"):
        for value in getattr(member_obj, field, None) or []:
            cleaned = str(value or "").strip()
            if cleaned:
                role_ids.append(cleaned)

    for field in ("roles", "role_names"):
        for value in getattr(member_obj, field, None) or []:
            if hasattr(value, "__dict__"):
                role_id = _extract_role_id(value)
                if role_id:
                    role_ids.append(role_id)

    return _unique_list(role_ids)


# ============================================================
# CACHE STALENESS
# ============================================================

def _is_stale(last_refresh: float | None, ttl_seconds: int) -> bool:
    return (_now_ts() - float(last_refresh or 0.0)) >= ttl_seconds


def _role_cache_is_stale() -> bool:
    return _is_stale(
        getattr(app_state, "vrc_group_roles_last_refresh", 0.0),
        GROUP_ROLE_REFRESH_TTL_SECONDS,
    )


def _member_cache_is_stale() -> bool:
    return _is_stale(
        getattr(app_state, "vrc_group_members_last_refresh", 0.0),
        GROUP_MEMBER_REFRESH_TTL_SECONDS,
    )


def _group_info_cache_is_stale() -> bool:
    return _is_stale(
        getattr(app_state, "vrc_group_info_last_refresh", 0.0),
        GROUP_INFO_REFRESH_TTL_SECONDS,
    )


def _pipeline_cache_is_stale() -> bool:
    last = float(getattr(app_state, "vrc_pipeline_last_event_ts", 0.0) or 0.0)
    return last <= 0 or (_now_ts() - last) >= PIPELINE_STALE_SECONDS


# ============================================================
# APP STATE SETUP
# ============================================================

def _ensure_attr_default(attr: str, default) -> None:
    if not hasattr(app_state, attr):
        setattr(app_state, attr, default() if callable(default) else default)


def _ensure_pipeline_state() -> None:
    _ensure_attr_default("vrc_pipeline_friend_presence", dict)
    _ensure_attr_default("vrc_pipeline_ws", None)
    _ensure_attr_default("vrc_pipeline_task", None)
    _ensure_attr_default("vrc_pipeline_last_event_ts", 0.0)
    _ensure_attr_default("vrc_pipeline_connected", False)


def _ensure_recent_activity_state() -> None:
    _ensure_attr_default("vrc_recent_activity", dict)
    _ensure_attr_default("vrc_friend_presence_cache", dict)
    _ensure_attr_default("vrc_friend_presence_last_refresh", 0.0)


def _ensure_vrc_sync_state() -> None:
    _ensure_attr_default("vrc_group_roles_last_error_ts", 0.0)
    _ensure_attr_default("vrc_group_members_last_error_ts", 0.0)
    _ensure_attr_default("vrc_group_info_last_error_ts", 0.0)
    _ensure_attr_default("vrc_group_roles_refresh_lock", asyncio.Lock)
    _ensure_attr_default("vrc_group_members_refresh_lock", asyncio.Lock)
    _ensure_attr_default("vrc_group_info_refresh_lock", asyncio.Lock)
    _ensure_attr_default("group_cache", dict)
    _ensure_attr_default("vrc_group_info_last_refresh", 0.0)
    _ensure_attr_default("vrc_group_member_role_ids", dict)
    _ensure_attr_default("vrchat_staff_role_ids", set)


# ============================================================
# ERROR / RETRY HELPERS
# ============================================================

def _is_connection_reset_error(exc: Exception) -> bool:
    text = str(exc or "")
    return (
        isinstance(exc, ConnectionResetError)
        or "ConnectionResetError" in text
        or "Connection aborted" in text
        or "forcibly closed by the remote host" in text
        or "Max retries exceeded" in text
        or "ProtocolError" in text
    )


async def _run_vrc_api_call(
    func,
    *args,
    retries: int = VRCHAT_API_RETRIES,
    base_delay: float = VRCHAT_API_BASE_DELAY_SECONDS,
    **kwargs,
):
    last_exc = None

    for attempt in range(1, retries + 1):
        try:
            return await run_blocking(func, *args, **kwargs)

        except Exception as exc:
            last_exc = exc

            if attempt >= retries:
                raise

            delay = base_delay * attempt
            func_name = getattr(func, "__name__", str(func))

            log.warning(
                "VRChat API retry %s/%s func=%s type=%s error=%s delay=%.1fs",
                attempt,
                retries,
                func_name,
                type(exc).__name__,
                exc,
                delay,
            )

            if attempt == 1 and _is_connection_reset_error(exc):
                await send_error_log(
                    "VRChat API Retry Warning",
                    exc,
                    severity="warning",
                    trace_id="vrchat_retry",
                    extra={
                        "attempt": f"{attempt}/{retries}",
                        "function": func_name,
                        "delay_seconds": f"{delay:.1f}",
                        "error_type": type(exc).__name__,
                    },
                )

            await asyncio.sleep(delay)

    raise last_exc


async def _send_rate_limited_error(
    title: str,
    exc: Exception,
    attr_name: str,
    cooldown_seconds: int = 300,
) -> None:
    _ensure_vrc_sync_state()

    now = _now_ts()
    last_sent = float(getattr(app_state, attr_name, 0.0) or 0.0)

    log.warning("%s: %s: %s", title, type(exc).__name__, exc)

    if (now - last_sent) < cooldown_seconds and _is_connection_reset_error(exc):
        return

    setattr(app_state, attr_name, now)

    await send_error_log(
        title,
        exc,
        severity="error",
        trace_id="vrchat_cache",
        extra={
            "error_type": type(exc).__name__,
        },
    )


__all__ = [
    "app_state",
    "log",
    "_USER_AGENT",
    "GROUP_ID",
    "VRC_STAFF_ROLE_NAMES",
    "vrchat_cooldown_active",
    "_now_ts",
    "_normalize_vrc_name",
    "_normalize_vrc_user_id",
    "_normalize_status_value",
    "_extract_first_str_attr",
    "_extract_role_name",
    "_extract_role_id",
    "_extract_member_display_name",
    "_extract_group_name",
    "_extract_group_id",
    "_fallback_user_name",
    "_unique_list",
    "_member_role_names_from_obj",
    "_member_role_ids_from_obj",
    "_is_stale",
    "_role_cache_is_stale",
    "_member_cache_is_stale",
    "_group_info_cache_is_stale",
    "_pipeline_cache_is_stale",
    "_ensure_attr_default",
    "_ensure_pipeline_state",
    "_ensure_recent_activity_state",
    "_ensure_vrc_sync_state",
    "_is_connection_reset_error",
    "_run_vrc_api_call",
    "_send_rate_limited_error",
]
