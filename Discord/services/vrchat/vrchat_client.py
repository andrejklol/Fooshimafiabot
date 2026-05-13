import logging
import time
from typing import Any

from core.cache import app_state

log = logging.getLogger("vrchat_client")


# ============================================================
# CONSTANTS
# ============================================================

_USER_AGENT = "FooshiMafiaBot/1.3 (contact: fooshimafia@gmail.com)"

GROUP_ROLE_REFRESH_TTL_SECONDS = 900
GROUP_MEMBER_REFRESH_TTL_SECONDS = 900
GROUP_INFO_REFRESH_TTL_SECONDS = 900

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


def _normalize_status_value(value: Any) -> str:
    return str(value or "").strip().lower()


# ============================================================
# GENERIC OBJECT FIELD EXTRACTORS
# ============================================================

def _extract_first_str_attr(obj: Any, field_names: tuple[str, ...]) -> str | None:

    for field in field_names:

        value = str(getattr(obj, field, None) or "").strip()

        if value:
            return value

    return None


def _extract_role_name(role_obj) -> str | None:

    return _extract_first_str_attr(
        role_obj,
        ("name", "display_name", "role_name"),
    )


def _extract_role_id(role_obj) -> str | None:

    return _extract_first_str_attr(
        role_obj,
        ("id", "role_id"),
    )


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

        (
            "name",
            "display_name",
            "displayName",
            "group_name",
        ),

    )


def _extract_group_id(group_obj) -> str | None:

    return _extract_first_str_attr(

        group_obj,

        (
            "id",
            "group_id",
            "groupId",
        ),

    )


def _extract_vrchat_avatar_url(user_obj) -> str | None:

    return _extract_first_str_attr(

        user_obj,

        (
            "currentAvatarThumbnailImageUrl",
            "userIcon",
            "profilePicOverride",
            "avatar_url",
        ),

    )


# ============================================================
# FALLBACKS
# ============================================================

def _fallback_user_name(user_id: str) -> str:

    short_id = str(user_id).replace("usr_", "")[:8]

    return f"User {short_id}"


def _unique_list(values: list[str]) -> list[str]:

    return list(set(values))


# ============================================================
# ROLE EXTRACTION
# ============================================================

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


# ============================================================
# EXPORTS
# ============================================================

__all__ = [

    "_USER_AGENT",

    "GROUP_INFO_REFRESH_TTL_SECONDS",

    "GROUP_MEMBER_REFRESH_TTL_SECONDS",

    "GROUP_MEMBERS_PAGE_DELAY_SECONDS",

    "GROUP_ROLE_REFRESH_TTL_SECONDS",

    "_extract_first_str_attr",

    "_extract_group_id",

    "_extract_group_name",

    "_extract_member_display_name",

    "_extract_role_id",

    "_extract_role_name",

    "_extract_vrchat_avatar_url",

    "_fallback_user_name",

    "_group_info_cache_is_stale",

    "_is_stale",

    "_member_cache_is_stale",

    "_member_role_ids_from_obj",

    "_member_role_names_from_obj",

    "_normalize_status_value",

    "_normalize_vrc_name",

    "_normalize_vrc_user_id",

    "_now_ts",

    "_role_cache_is_stale",

    "_unique_list",

]
