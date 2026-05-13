import asyncio
import logging

from core.cache import app_state
from core.config import VRC_CONFIG
from core.utils import vrchat_cooldown_active

from .vrchat_auth import (
    _ensure_vrc_sync_state,
    _run_vrc_api_call,
    _send_rate_limited_error,
)
from .vrchat_client import (
    GROUP_MEMBERS_PAGE_DELAY_SECONDS,
    _extract_group_id,
    _extract_group_name,
    _extract_member_display_name,
    _extract_role_id,
    _extract_role_name,
    _fallback_user_name,
    _group_info_cache_is_stale,
    _member_cache_is_stale,
    _member_role_ids_from_obj,
    _member_role_names_from_obj,
    _normalize_vrc_name,
    _normalize_vrc_user_id,
    _now_ts,
    _role_cache_is_stale,
)

log = logging.getLogger("vrchat_group")

# ============================================================
# ROLE / STAFF HELPERS
# ============================================================

def get_cached_vrc_user_roles(user_id: str) -> list[str]:
    cleaned_user_id = str(user_id or "").strip()
    if not cleaned_user_id:
        return []
    return app_state.vrc_group_member_roles.get(cleaned_user_id, [])


def get_cached_vrc_user_role_ids(user_id: str) -> list[str]:
    cleaned_user_id = str(user_id or "").strip()
    if not cleaned_user_id:
        return []
    return app_state.vrc_group_member_role_ids.get(cleaned_user_id, [])


def get_pretty_vrc_name(user_id: str) -> str:
    cleaned_user_id = str(user_id or "").strip()
    return str(app_state.target_name_cache.get(cleaned_user_id) or _fallback_user_name(cleaned_user_id)).strip()


def resolve_vrchat_user_id(username_or_id: str) -> str | None:
    cleaned = str(username_or_id or "").strip()
    if not cleaned:
        return None
    if cleaned.lower().startswith("usr_"):
        return cleaned
    return app_state.vrc_username_to_id_cache.get(cleaned.lower())


def is_cached_vrc_user_staff(user_id: str) -> bool:
    uid = str(user_id or "").strip()
    if not uid:
        return False
    wanted_roles = {str(role).strip().casefold() for role in VRC_CONFIG.get('staff_role_names', [])}
    staff_role_ids = {str(role_id).strip() for role_id in getattr(app_state, "vrchat_staff_role_ids", set())}
    
    user_roles = [str(r).strip().casefold() for r in get_cached_vrc_user_roles(uid)]
    user_role_ids = [str(rid).strip() for rid in get_cached_vrc_user_role_ids(uid)]

    if any(any(w in r for w in wanted_roles) for r in user_roles):
        return True
    if any(rid in staff_role_ids for rid in user_role_ids):
        return True
    return False


async def vrc_user_is_staff(user_id: str) -> bool:
    await ensure_vrc_group_cache_ready()
    return is_cached_vrc_user_staff(user_id)


# ============================================================
# API STORAGE SYNCHRONIZATION 
# ============================================================

async def ensure_vrc_group_cache_ready() -> None:
    _ensure_vrc_sync_state()
    if _role_cache_is_stale():
        await refresh_vrc_group_roles()
    if _member_cache_is_stale():
        await refresh_vrc_group_members()


async def refresh_group_cache_once(force: bool = False) -> None:
    _ensure_vrc_sync_state()
    if force or _role_cache_is_stale():
        await refresh_vrc_group_roles()
    if force or _member_cache_is_stale():
        await refresh_vrc_group_members(force=force)


async def refresh_vrc_group_roles() -> bool:
    if not getattr(app_state, "vrc_groups_api", None):
        return False
    
    group_id = VRC_CONFIG.get('group_id')
    if not group_id:
        return False

    try:
        roles = await _run_vrc_api_call(app_state.vrc_groups_api.get_group_roles, group_id)
        staff_ids = set()
        wanted_roles = {str(role).strip().casefold() for role in VRC_CONFIG.get('staff_role_names', [])}

        for role in (roles or []):
            r_name = _extract_role_name(role)
            r_id = _extract_role_id(role)
            if r_id and r_name:
                if any(w in r_name.casefold() for w in wanted_roles):
                    staff_ids.add(r_id)

        app_state.vrchat_staff_role_ids = staff_ids
        app_state.vrc_group_roles_last_refresh = _now_ts()
        return True
    except Exception as exc:
        log.error(f"Failed fetching group roles: {exc}")
        return False


async def refresh_vrc_group_members(force: bool = False) -> bool:
    if not getattr(app_state, "vrc_groups_api", None):
        return False
    
    group_id = VRC_CONFIG.get('group_id')
    if not group_id:
        return False

    if not force and vrchat_cooldown_active():
        return False

    try:
        offset = 0
        limit = 100
        all_roles = {}
        all_role_ids = {}

        while True:
            members = await _run_vrc_api_call(
                app_state.vrc_groups_api.get_group_members,
                group_id,
                n=limit,
                offset=offset
            )
            if not members:
                break

            for m in members:
                uid = _normalize_vrc_user_id(getattr(m, "user_id", None))
                d_name = _extract_member_display_name(m)
                if uid:
                    all_roles[uid] = _member_role_names_from_obj(m)
                    all_role_ids[uid] = _member_role_ids_from_obj(m)
                    if d_name:
                        app_state.target_name_cache[uid] = d_name
                        app_state.vrc_username_to_id_cache[d_name.lower()] = uid

            if len(members) < limit:
                break
            offset += limit
            await asyncio.sleep(GROUP_MEMBERS_PAGE_DELAY_SECONDS)

        app_state.vrc_group_member_roles = all_roles
        app_state.vrc_group_member_role_ids = all_role_ids
        app_state.vrc_group_members_last_refresh = _now_ts()
        return True
    except Exception as exc:
        log.error(f"Failed pulling group members pagination loop: {exc}")
        return False


async def get_all_vrc_staff_members(force_refresh: bool = False) -> list[dict]:
    if force_refresh:
        await refresh_group_cache_once(force=True)
    else:
        await ensure_vrc_group_cache_ready()

    wanted_roles = {str(role).strip().casefold() for role in VRC_CONFIG.get('staff_role_names', [])}
    staff_role_ids = {str(role_id).strip() for role_id in getattr(app_state, "vrchat_staff_role_ids", set())}

    results: list[dict] = []
    for user_id, roles in (app_state.vrc_group_member_roles or {}).items():
        normalized_roles = [str(r).strip().casefold() for r in (roles or []) if str(r).strip()]
        normalized_role_ids = [str(rid).strip() for rid in (app_state.vrc_group_member_role_ids.get(user_id, []) or [])]

        if any(any(w in r for w in wanted_roles) for r in normalized_roles) or any(rid in staff_role_ids for rid in normalized_role_ids):
            results.append({
                "user_id": str(user_id),
                "display_name": get_pretty_vrc_name(user_id),
                "roles": normalized_roles,
                "role_ids": normalized_role_ids,
            })

    results.sort(key=lambda entry: entry["display_name"].casefold())
    return results
