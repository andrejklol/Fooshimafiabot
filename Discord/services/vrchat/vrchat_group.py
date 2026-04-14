from core.cache import app_state

from . import (
    GROUP_ID,
    GROUP_MEMBERS_PAGE_DELAY_SECONDS,
    VRC_STAFF_ROLE_NAMES,
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
    _run_vrc_api_call,
    _send_rate_limited_error,
    _ensure_vrc_sync_state,
    log,
    vrchat_cooldown_active,
)
import asyncio


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


def is_cached_vrc_user_staff(user_id: str) -> bool:
    wanted = {str(x).strip().casefold() for x in VRC_STAFF_ROLE_NAMES}
    staff_role_ids = {
        str(x).strip()
        for x in getattr(app_state, "vrchat_staff_role_ids", set())
    }

    role_name_match = any(
        any(wanted_role in str(role).strip().casefold() for wanted_role in wanted)
        for role in get_cached_vrc_user_roles(user_id)
    )

    role_id_match = any(
        str(role_id).strip() in staff_role_ids
        for role_id in get_cached_vrc_user_role_ids(user_id)
    )

    return role_name_match or role_id_match


async def vrc_user_is_staff(user_id: str) -> bool:
    cleaned_user_id = str(user_id or "").strip()
    if not cleaned_user_id:
        return False

    if is_cached_vrc_user_staff(cleaned_user_id):
        return True

    if not app_state.vrc_group_member_roles:
        await ensure_vrc_group_cache_ready()

    if is_cached_vrc_user_staff(cleaned_user_id):
        return True

    if _member_cache_is_stale():
        await refresh_vrc_group_members(force=True)

    return is_cached_vrc_user_staff(cleaned_user_id)


async def get_pretty_vrc_name(entry) -> tuple[str, str]:
    target_id = str(getattr(entry, "target_id", "") or "").strip()
    if not target_id:
        return "Unknown User", "N/A"

    for field in ("target_display_name", "target_username", "target_name", "display_name"):
        value = str(getattr(entry, field, None) or "").strip()
        if value:
            app_state.target_name_cache[target_id] = value
            return value, target_id

    cached = app_state.target_name_cache.get(target_id)
    if cached:
        return cached, target_id

    short_id = target_id.replace("usr_", "")[:8]
    return f"User {short_id}", target_id


# ============================================================
# GROUP CACHE
# ============================================================

async def refresh_group_cache_once(force: bool = False) -> None:
    if not app_state.vrc_groups_api or vrchat_cooldown_active():
        return

    _ensure_vrc_sync_state()

    async with app_state.vrc_group_info_refresh_lock:
        if not force and app_state.group_cache and not _group_info_cache_is_stale():
            return

        try:
            group = await _run_vrc_api_call(
                app_state.vrc_groups_api.get_group,
                GROUP_ID,
            )

            if group is None:
                log.warning("group_info refresh returned no group object; keeping existing cache")
                return

            group_id = _extract_group_id(group) or str(GROUP_ID)
            group_name = _extract_group_name(group)

            if not group_name:
                log.warning("group_info refresh returned no group name; keeping existing cache")
                return

            app_state.group_cache[group_id] = {
                "id": group_id,
                "name": group_name,
                "displayName": group_name,
            }
            app_state.vrc_group_info_last_refresh = _now_ts()

            if hasattr(app_state, "sync_cache_aliases"):
                app_state.sync_cache_aliases()

            log.info("cached group info id=%s name=%s", group_id, group_name)

        except Exception as exc:
            await _send_rate_limited_error(
                "Group Cache Error",
                exc,
                "vrc_group_info_last_error_ts",
            )


async def refresh_vrc_group_roles(force: bool = False) -> None:
    if not app_state.vrc_groups_api or vrchat_cooldown_active():
        return

    _ensure_vrc_sync_state()

    async with app_state.vrc_group_roles_refresh_lock:
        if not force and app_state.vrc_group_role_map and not _role_cache_is_stale():
            return

        try:
            roles_response = await _run_vrc_api_call(
                app_state.vrc_groups_api.get_group_roles,
                group_id=GROUP_ID,
            )

            role_objects = getattr(roles_response, "results", None)
            if role_objects is None:
                role_objects = roles_response if isinstance(roles_response, list) else []

            new_map = {
                str(role_id): role_name
                for obj in role_objects
                if (role_id := _extract_role_id(obj)) and (role_name := _extract_role_name(obj))
            }

            if not new_map:
                log.warning("group_roles refresh returned empty role map; keeping existing cache")
                return

            old_count = len(getattr(app_state, "vrc_group_role_map", {}))
            app_state.vrc_group_role_map = new_map
            app_state.vrc_group_roles_last_refresh = _now_ts()
            app_state.vrchat_group_roles = new_map

            try:
                role_names_lower = {str(x).strip().casefold() for x in VRC_STAFF_ROLE_NAMES}
                staff_role_ids = {
                    role_id
                    for role_id, role_name in new_map.items()
                    if any(
                        wanted_role in str(role_name).strip().casefold()
                        for wanted_role in role_names_lower
                    )
                }
                app_state.vrchat_staff_role_ids = staff_role_ids
            except Exception:
                pass

            if hasattr(app_state, "sync_cache_aliases"):
                app_state.sync_cache_aliases()

            if old_count != len(new_map) or old_count == 0:
                log.info("cached %s VRC group roles", len(new_map))
            else:
                log.debug("group_roles refresh complete count=%s", len(new_map))

        except Exception as exc:
            await _send_rate_limited_error(
                "Group Role Cache Error",
                exc,
                "vrc_group_roles_last_error_ts",
            )


async def refresh_vrc_group_members(force: bool = False) -> None:
    if not app_state.vrc_groups_api or vrchat_cooldown_active():
        return

    _ensure_vrc_sync_state()

    async with app_state.vrc_group_members_refresh_lock:
        if not force and app_state.vrc_group_member_roles and not _member_cache_is_stale():
            return

        try:
            await refresh_group_cache_once(force=force)
            await refresh_vrc_group_roles(force=force)

            offset = 0
            batch_size = 100

            old_role_cache = dict(getattr(app_state, "vrc_group_member_roles", {}) or {})
            old_role_id_cache = dict(getattr(app_state, "vrc_group_member_role_ids", {}) or {})

            new_role_cache: dict[str, list[str]] = {}
            new_role_id_cache: dict[str, list[str]] = {}

            total_rows = 0
            missing_user_id = 0
            duplicate_user_ids = 0
            page_count = 0
            consecutive_no_growth_pages = 0

            while True:
                members_response = await _run_vrc_api_call(
                    app_state.vrc_groups_api.get_group_members,
                    group_id=GROUP_ID,
                    n=batch_size,
                    offset=offset,
                )

                members = getattr(members_response, "results", None)
                if members is None:
                    members = members_response if isinstance(members_response, list) else []

                if not members:
                    break

                page_count += 1
                before_count = len(new_role_cache)

                for member in members:
                    total_rows += 1

                    user_id = str(
                        getattr(member, "user_id", None)
                        or getattr(member, "id", None)
                        or ""
                    ).strip()

                    if not user_id:
                        missing_user_id += 1
                        continue

                    if user_id in new_role_cache:
                        duplicate_user_ids += 1

                    new_role_cache[user_id] = _member_role_names_from_obj(member)
                    new_role_id_cache[user_id] = _member_role_ids_from_obj(member)

                    display_name = _extract_member_display_name(member)
                    if display_name:
                        app_state.target_name_cache[user_id] = display_name

                loaded_now = len(members)
                growth = len(new_role_cache) - before_count

                log.debug(
                    "group_members page=%s loaded=%s growth=%s offset_before=%s cache_size=%s",
                    page_count,
                    loaded_now,
                    growth,
                    offset,
                    len(new_role_cache),
                )

                consecutive_no_growth_pages = (
                    consecutive_no_growth_pages + 1 if growth <= 0 else 0
                )
                offset += loaded_now

                if loaded_now < batch_size:
                    break

                if consecutive_no_growth_pages >= 2:
                    log.warning(
                        "group_members stopping early due to repeated no-growth pages "
                        "(pages=%s cache_size=%s duplicates=%s)",
                        page_count,
                        len(new_role_cache),
                        duplicate_user_ids,
                    )
                    break

                await asyncio.sleep(GROUP_MEMBERS_PAGE_DELAY_SECONDS)

            if not new_role_cache:
                log.warning("group_members refresh produced empty cache; keeping old cache")
                return

            old_count = len(old_role_cache)
            if old_count > 0 and len(new_role_cache) < max(50, int(old_count * 0.60)):
                log.warning(
                    "group_members refresh looked partial; keeping old cache "
                    "(new=%s old=%s api_rows=%s duplicates=%s pages=%s)",
                    len(new_role_cache),
                    old_count,
                    total_rows,
                    duplicate_user_ids,
                    page_count,
                )
                return

            wanted_roles = {str(x).strip().casefold() for x in VRC_STAFF_ROLE_NAMES}
            staff_role_ids = {
                str(x).strip() for x in getattr(app_state, "vrchat_staff_role_ids", set())
            }

            preserved_staff = 0
            for user_id, old_roles in old_role_cache.items():
                if user_id in new_role_cache:
                    continue

                old_role_ids = old_role_id_cache.get(user_id, [])

                old_is_staff = any(
                    any(wanted_role in str(role).strip().casefold() for wanted_role in wanted_roles)
                    for role in (old_roles or [])
                ) or any(
                    str(role_id).strip() in staff_role_ids
                    for role_id in (old_role_ids or [])
                )

                if old_is_staff:
                    new_role_cache[user_id] = list(old_roles or [])
                    new_role_id_cache[user_id] = list(old_role_ids or [])
                    preserved_staff += 1

            app_state.vrc_group_member_roles = new_role_cache
            app_state.vrc_group_member_role_ids = new_role_id_cache
            app_state.vrc_group_members_last_refresh = _now_ts()
            app_state.vrchat_group_members = new_role_cache

            if hasattr(app_state, "sync_cache_aliases"):
                app_state.sync_cache_aliases()

            log.info(
                "cached %s VRC group members "
                "(api_rows=%s missing_user_id=%s duplicates=%s pages=%s old_cache=%s preserved_staff=%s)",
                len(new_role_cache),
                total_rows,
                missing_user_id,
                duplicate_user_ids,
                page_count,
                old_count,
                preserved_staff,
            )

        except Exception as exc:
            await _send_rate_limited_error(
                "Group Member Cache Error",
                exc,
                "vrc_group_members_last_error_ts",
            )


async def ensure_vrc_group_cache_ready() -> None:
    if not app_state.vrc_group_member_roles and not vrchat_cooldown_active():
        await refresh_vrc_group_members(force=True)
        return

    if not getattr(app_state, "group_cache", None) and not vrchat_cooldown_active():
        await refresh_group_cache_once(force=True)


# ============================================================
# USER RESOLUTION
# ============================================================

async def resolve_vrchat_user_id(
    vrchat_username: str | None = None,
    vrchat_user_id: str | None = None,
) -> str | None:
    explicit_id = _normalize_vrc_user_id(vrchat_user_id)
    if explicit_id:
        return explicit_id

    wanted_name = _normalize_vrc_name(vrchat_username)
    if not wanted_name:
        return None

    if not app_state.vrc_group_member_roles:
        await ensure_vrc_group_cache_ready()

    def _scan_cache() -> str | None:
        return next(
            (
                uid
                for uid in app_state.vrc_group_member_roles
                if _normalize_vrc_name(app_state.target_name_cache.get(uid)) == wanted_name
            ),
            None,
        )

    result = _scan_cache()
    if result:
        return result

    if _member_cache_is_stale() and not vrchat_cooldown_active():
        await refresh_vrc_group_members(force=True)
        return _scan_cache()

    return None


# ============================================================
# STAFF LIST FROM VRC GROUP
# ============================================================

async def get_all_vrc_staff_members(force_refresh: bool = False) -> list[dict]:
    if force_refresh:
        await refresh_vrc_group_members(force=True)
    else:
        await ensure_vrc_group_cache_ready()

        if _member_cache_is_stale():
            await refresh_vrc_group_members(force=True)

    wanted_roles = {str(role).strip().casefold() for role in VRC_STAFF_ROLE_NAMES}
    staff_role_ids = {
        str(role_id).strip()
        for role_id in getattr(app_state, "vrchat_staff_role_ids", set())
    }

    results: list[dict] = []

    for user_id, roles in (app_state.vrc_group_member_roles or {}).items():
        normalized_roles = [
            str(role).strip().casefold()
            for role in (roles or [])
            if str(role).strip()
        ]
        normalized_role_ids = [
            str(role_id).strip()
            for role_id in (app_state.vrc_group_member_role_ids.get(user_id, []) or [])
            if str(role_id).strip()
        ]

        has_staff_role = any(
            any(wanted in role for wanted in wanted_roles)
            for role in normalized_roles
        )
        has_staff_role_id = any(role_id in staff_role_ids for role_id in normalized_role_ids)

        if not (has_staff_role or has_staff_role_id):
            continue

        display_name = str(
            app_state.target_name_cache.get(user_id) or _fallback_user_name(user_id)
        ).strip()

        results.append(
            {
                "user_id": str(user_id),
                "display_name": display_name,
                "roles": normalized_roles,
                "role_ids": normalized_role_ids,
            }
        )

    results.sort(key=lambda entry: entry["display_name"].casefold())

    log.info("VRC staff sync found %s staff members", len(results))
    return results
