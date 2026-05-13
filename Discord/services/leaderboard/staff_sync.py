from __future__ import annotations

import unicodedata
from typing import Any

from core.cache import app_state
from core.config import GUILD_ID, STAFF_ALERT_ORDER, VRC_STAFF_ROLE_NAMES
from services.vrchat import get_all_vrc_staff_members

from .processors import sync_staff_archive_state
from .storage import leaderboard_data, save_leaderboard_data


# -------------------------
# INTERNAL HELPERS
# -------------------------

def _ensure_section(section: str) -> None:
    if section not in leaderboard_data or not isinstance(leaderboard_data[section], dict):
        leaderboard_data[section] = {}


def _normalize_name_key(value: str | None) -> str:
    """Normalize names for fuzzy-safe exact matching.

    Uses Unicode NFKC compatibility normalization BEFORE stripping to
    non-alphanumerics. This folds stylized characters (mathematical
    bold/italic/double-struck, full-width, superscript, etc.) to their
    canonical equivalents.
    """
    text = str(value or "")
    text = unicodedata.normalize("NFKC", text)
    text = text.strip().lower()
    return "".join(ch for ch in text if ch.isalnum())


def _is_placeholder_name(name: str | None) -> bool:
    text = str(name or "").strip()
    lowered = text.lower()
    return (
        not text
        or text.startswith("User ")
        or lowered in {"unknown", "unknown user", "n/a", "-", "none", "null"}
    )


def _clean_optional_string(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text or text.lower() == "null":
        return None
    return text


def _default_entry(name: str) -> dict[str, Any]:
    return {
        "id": None,
        "name": name,
        "warn": 0,
        "kick": 0,
        "ban": 0,
        "invite": 0,
        "invite_accept": 0,
        "points": 0,
        "rank_name": "Unknown Rank",
        "vrchat_username": name,
        "discord_avatar_url": None,
    }


def _existing_best_name(user_id: str) -> str | None:
    user_key = str(user_id)
    
    for section_name in ("staff", "monthly", "archive"):
        entry = leaderboard_data.get(section_name, {}).get(user_key)
        if isinstance(entry, dict):
            for key in ("name", "vrchat_username", "discord_username"):
                val = str(entry.get(key) or "").strip()
                if val and not _is_placeholder_name(val):
                    return val

    cached = getattr(app_state, "target_name_cache", {}).get(user_key)
    cached = str(cached or "").strip()
    if cached and not _is_placeholder_name(cached):
        return cached

    return None


def _choose_display_name(user_id: str, *candidates: str | None) -> str:
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text and not _is_placeholder_name(text):
            return text

    existing = _existing_best_name(user_id)
    if existing:
        return existing

    return f"User {str(user_id).replace('usr_', '')[:8]}"


def _preserve_existing_stats(existing: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    """Merge defaults into an existing entry WITHOUT resetting counters."""
    merged = dict(existing) if isinstance(existing, dict) else {}
    for key, value in defaults.items():
        merged.setdefault(key, value)
    return merged


def _ensure_staff_entry(
    section: str,
    user_id: str,
    display_name: str,
    rank_name: str | None = None,
    *,
    discord_id: str | None = None,
    discord_username: str | None = None,
    discord_avatar_url: str | None = None,
    vrchat_username: str | None = None,
    vrchat_avatar_url: str | None = None,
) -> None:
    _ensure_section(section)
    user_key = str(user_id)
    existing = leaderboard_data[section].get(user_key)

    chosen_name = _choose_display_name(user_key, display_name, vrchat_username, discord_username)

    # Clean incoming data structures cleanly
    cleaned = {
        "id": user_key,
        "discord_id": _clean_optional_string(discord_id),
        "discord_username": _clean_optional_string(discord_username),
        "discord_avatar_url": _clean_optional_string(discord_avatar_url),
        "vrchat_username": _clean_optional_string(vrchat_username),
        "vrchat_avatar_url": _clean_optional_string(vrchat_avatar_url),
    }

    defaults = _default_entry(chosen_name)
    if rank_name:
        defaults["rank_name"] = str(rank_name).strip()

    # Drop optional fields from defaults if they are missing/placeholders
    for key in ("vrchat_username", "discord_username"):
        if cleaned[key] and not _is_placeholder_name(cleaned[key]):
            defaults[key] = cleaned[key]
    for key in ("discord_id", "discord_avatar_url", "vrchat_avatar_url"):
        if cleaned[key]:
            defaults[key] = cleaned[key]

    if not isinstance(existing, dict):
        leaderboard_data[section][user_key] = defaults
        return

    merged = _preserve_existing_stats(existing, defaults)

    # Resolve Display Name overrides safely
    existing_name = str(merged.get("name") or "").strip()
    if not (_is_placeholder_name(chosen_name) and existing_name and not _is_placeholder_name(existing_name)):
        merged["name"] = chosen_name

    # Cascade sync identities without dumping historical state values
    if cleaned["vrchat_username"] and not _is_placeholder_name(cleaned["vrchat_username"]):
        merged["vrchat_username"] = cleaned["vrchat_username"]
    elif _is_placeholder_name(merged.get("vrchat_username")):
        merged["vrchat_username"] = chosen_name

    if rank_name:
        merged["rank_name"] = str(rank_name).strip()

    # Dynamic attribute mapper safely popping off structural empty data fields
    for field in ("discord_id", "discord_username", "discord_avatar_url", "vrchat_avatar_url"):
        if cleaned[field]:
            merged[field] = cleaned[field]
        elif existing.get(field):
            merged[field] = existing[field]
        else:
            merged.pop(field, None)

    merged["id"] = user_key
    leaderboard_data[section][user_key] = merged


def _normalize_role_name(value: str | None) -> str:
    return str(value or "").strip().lower()


def _has_staff_role(member: Any) -> bool:
    staff_role_names = {_normalize_role_name(name) for name in VRC_STAFF_ROLE_NAMES}
    for role in getattr(member, "roles", []):
        role_name = _normalize_role_name(getattr(role, "name", None))
        if role_name in staff_role_names:
            return True
    return False


def _get_bot_guild() -> Any | None:
    bot = getattr(app_state, "bot", None)
    if bot is None:
        return None

    guild = None
    if GUILD_ID:
        try:
            guild = bot.get_guild(int(GUILD_ID))
        except Exception:
            pass

    if guild is None:
        guilds = getattr(bot, "guilds", None) or []
        if len(guilds) == 1:
            guild = guilds[0]

    return guild


def _first_non_empty(*values: Any) -> str | None:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def _extract_vrchat_user_id(staff_item: dict[str, Any]) -> str | None:
    return _first_non_empty(
        staff_item.get("user_id"),
        staff_item.get("vrchat_id"),
        staff_item.get("id"),
        staff_item.get("userId"),
        staff_item.get("vrchatUserId"),
    )


def _extract_discord_id(staff_item: dict[str, Any]) -> str | None:
    return _first_non_empty(
        staff_item.get("discord_id"),
        staff_item.get("discordId"),
        staff_item.get("discord_user_id"),
        staff_item.get("staff_discord_id"),
        staff_item.get("staff_id"),
    )


def _extract_vrchat_display_name(staff_item: dict[str, Any]) -> str | None:
    return _first_non_empty(
        staff_item.get("vrchat_username"),
        staff_item.get("vrchat_name"),
        staff_item.get("vrchat_display_name"),
        staff_item.get("display_name"),
        staff_item.get("displayName"),
        staff_item.get("name"),
        staff_item.get("username"),
    )


def _extract_rank_name(staff_item: dict[str, Any]) -> str | None:
    return _first_non_empty(
        staff_item.get("rank_name"),
        staff_item.get("rank"),
        staff_item.get("role_name"),
        staff_item.get("role"),
    )


def _extract_vrchat_avatar_url(staff_item: dict[str, Any]) -> str | None:
    return _first_non_empty(
        staff_item.get("vrchat_avatar_url"),
        staff_item.get("currentAvatarThumbnailImageUrl"),
        staff_item.get("current_avatar_thumbnail_image_url"),
        staff_item.get("avatar_thumbnail_url"),
        staff_item.get("thumbnailUrl"),
        staff_item.get("thumbnail_url"),
        staff_item.get("profile_image_url"),
        staff_item.get("user_icon"),
        staff_item.get("icon_url"),
    )


def _cache_best_name(user_id: str, display_name: str | None) -> None:
    if not user_id or _is_placeholder_name(display_name):
        return

    cache = getattr(app_state, "target_name_cache", None)
    if not isinstance(cache, dict):
        app_state.target_name_cache = {}
        cache = app_state.target_name_cache

    cache[str(user_id)] = str(display_name).strip()


def _get_member_best_name(member: Any) -> str | None:
    return _first_non_empty(
        getattr(member, "global_name", None),
        getattr(member, "display_name", None),
        getattr(member, "nick", None),
        getattr(member, "name", None),
    )


def _get_member_avatar_url(member: Any) -> str | None:
    for attr in ("display_avatar", "avatar"):
        avatar = getattr(member, attr, None)
        if avatar is not None:
            try:
                return str(avatar.url)
            except Exception:
                pass
    return None


def _get_member_by_discord_id(discord_id: str | None) -> Any | None:
    cleaned = str(discord_id or "").strip()
    guild = _get_bot_guild()
    if not cleaned or guild is None:
        return None

    try:
        return guild.get_member(int(cleaned))
    except Exception:
        return None


def _iter_known_staff_entries() -> list[tuple[str, dict[str, Any]]]:
    seen: set[str] = set()
    results: list[tuple[str, dict[str, Any]]] = []

    for section_name in ("staff", "archive", "monthly"):
        section = leaderboard_data.get(section_name, {})
        if not isinstance(section, dict):
            continue

        for user_id, entry in section.items():
            user_key = str(user_id)
            if user_key in seen or not isinstance(entry, dict):
                continue
            seen.add(user_key)
            results.append((user_key, entry))

    return results


def _staff_alert_order_pairs() -> list[dict[str, Any]]:
    """Flatten STAFF_ALERT_ORDER into a list of authoritative identity seeds."""
    seen: set[tuple[str, str]] = set()
    pairs: list[dict[str, Any]] = []
    
    for action_groups in STAFF_ALERT_ORDER.values():
        for _rank_name, members in action_groups:
            for member in members:
                if not isinstance(member, dict):
                    continue
                vrc_id = str(member.get("vrchat_user_id") or "").strip()
                discord_id = str(member.get("discord_id") or "").strip()
                if not vrc_id or not discord_id:
                    continue
                key = (vrc_id, discord_id)
                if key in seen:
                    continue
                seen.add(key)
                pairs.append({"user_id": vrc_id, "discord_id": discord_id})
    return pairs


def _get_discord_identity_map(staff_members: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build a mapping of vrchat_user_id -> discord identity info."""
    result: dict[str, dict[str, Any]] = {}
    guild = _get_bot_guild()

    all_candidates: list[dict[str, Any]] = []
    all_candidates.extend(_staff_alert_order_pairs())
    all_candidates.extend([m for m in staff_members if isinstance(m, dict)])

    cached_vrc_staff = getattr(app_state, "vrc_staff", None)
    if isinstance(cached_vrc_staff, list):
        all_candidates.extend([m for m in cached_vrc_staff if isinstance(m, dict)])

    for user_id, entry in _iter_known_staff_entries():
        all_candidates.append({
            "user_id": user_id,
            "discord_id": entry.get("discord_id"),
            "vrchat_username": entry.get("vrchat_username") or entry.get("name"),
            "vrchat_avatar_url": entry.get("vrchat_avatar_url"),
        })

    for member in all_candidates:
        vrchat_user_id = _extract_vrchat_user_id(member)
        if not vrchat_user_id:
            continue

        vrchat_name = _extract_vrchat_display_name(member)
        vrchat_avatar_url = _extract_vrchat_avatar_url(member)
        discord_id = _extract_discord_id(member)

        identity = result.setdefault(vrchat_user_id, {
            "discord_id": None,
            "discord_username": None,
            "has_discord_role": None,
            "discord_avatar_url": None,
            "vrchat_avatar_url": vrchat_avatar_url,
            "vrchat_username": vrchat_name,
        })

        if vrchat_avatar_url and not identity.get("vrchat_avatar_url"):
            identity["vrchat_avatar_url"] = vrchat_avatar_url

        if vrchat_name and not identity.get("vrchat_username"):
            identity["vrchat_username"] = vrchat_name

        if discord_id and not identity.get("discord_id"):
            identity["discord_id"] = discord_id

    if guild is None:
        return result

    by_id: dict[str, Any] = {}
    by_name_key: dict[str, Any] = {}

    for member in getattr(guild, "members", []):
        if not _has_staff_role(member):
            continue

        discord_id = str(getattr(member, "id", "")).strip()
        if discord_id:
            by_id[discord_id] = member

        for candidate_name in (
            getattr(member, "global_name", None),
            getattr(member, "display_name", None),
            getattr(member, "nick", None),
            getattr(member, "name", None),
        ):
            key = _normalize_name_key(candidate_name)
            if key and key not in by_name_key:
                by_name_key[key] = member

    # Primary Pass: Resolve by authoritative IDs
    for vrchat_user_id, identity in result.items():
        discord_id = str(identity.get("discord_id") or "").strip()
        if not discord_id:
            continue

        member = by_id.get(discord_id)
        if member is None:
            identity["has_discord_role"] = False
            continue

        identity.update({
            "has_discord_role": True,
            "discord_username": _get_member_best_name(member),
            "discord_avatar_url": _get_member_avatar_url(member),
        })

    # Fallback Pass: Resolve by stylized-safe Unicode keys
    for vrchat_user_id, identity in result.items():
        if identity.get("discord_id"):
            continue

        name_key = _normalize_name_key(identity.get("vrchat_username"))
        member = by_name_key.get(name_key) if name_key else None
        if member is None:
            continue

        discord_id = str(getattr(member, "id", "")).strip()
        if discord_id:
            identity.update({
                "discord_id": discord_id,
                "discord_username": _get_member_best_name(member),
                "discord_avatar_url": _get_member_avatar_url(member),
                "has_discord_role": True,
            })

    return result


# -------------------------
# MAIN SYNC
# -------------------------

async def sync_staff_from_vrc_group(force_refresh: bool = False) -> int:
    """Sync staff from VRChat group AND Discord roles.

    Archive triggers ONLY when BOTH roles are gone.
    Preserves existing warns/kicks/bans/points instead of resetting them.
    """
    staff_payload = await get_all_vrc_staff_members(force_refresh=force_refresh)
    staff_members: list[dict[str, Any]] = list(staff_payload) if isinstance(staff_payload, list) else []

    discord_identity_map = _get_discord_identity_map(staff_members)

    for sect in ("staff", "monthly", "archive"):
        _ensure_section(sect)

    count = 0
    vrc_ids: set[str] = set()

    for member in staff_members:
        if not isinstance(member, dict):
            continue

        user_id = _extract_vrchat_user_id(member)
        if not user_id:
            continue
        
        vrc_ids.add(user_id)

        raw_vrchat_name = _extract_vrchat_display_name(member)
        rank_name = _extract_rank_name(member)
        vrchat_avatar_url = _extract_vrchat_avatar_url(member)

        identity = discord_identity_map.get(user_id, {})
        discord_id = _first_non_empty(identity.get("discord_id"), _extract_discord_id(member))
        discord_username = identity.get("discord_username")
        discord_avatar_url = identity.get("discord_avatar_url")
        has_discord_role = identity.get("has_discord_role")

        display_name = _choose_display_name(user_id, raw_vrchat_name, discord_username)
        _cache_best_name(user_id, display_name)

        archive_result = sync_staff_archive_state(
            user_id=user_id,
            username=display_name,
            rank_name=rank_name,
            has_vrc_staff_role=True,
            has_discord_staff_role=has_discord_role,
        )

        if archive_result.get("status") != "archived":
            write_payload = {
                "rank_name": rank_name,
                "discord_id": discord_id,
                "discord_username": discord_username,
                "discord_avatar_url": discord_avatar_url,
                "vrchat_username": raw_vrchat_name or display_name,
                "vrchat_avatar_url": vrchat_avatar_url,
            }
            _ensure_staff_entry("staff", user_id, display_name, **write_payload)
            _ensure_staff_entry("monthly", user_id, display_name, **write_payload)

        count += 1

    # Prune and sync missing staffers who left VRC group but hold Discord roles
    existing_ids = set(leaderboard_data.get("staff", {}).keys())
    missing_ids = existing_ids - vrc_ids

    for user_id in missing_ids:
        user_key = str(user_id)
        identity = discord_identity_map.get(user_key, {})
        has_discord_role = identity.get("has_discord_role")

        if has_discord_role is None:
            stored_entry = next((
                leaderboard_data[s].get(user_key)
                for s in ("staff", "archive", "monthly")
                if isinstance(leaderboard_data.get(s), dict) and user_key in leaderboard_data[s]
            ), {})
            
            stored_discord_id = _first_non_empty(
                identity.get("discord_id"), 
                stored_entry.get("discord_id") if isinstance(stored_entry, dict) else None
            )
            member = _get_member_by_discord_id(stored_discord_id)
            has_discord_role = bool(member and _has_staff_role(member))
            
            if stored_discord_id and not identity.get("discord_id"):
                identity["discord_id"] = stored_discord_id
            if member is not None:
                identity.update({
                    "discord_username": _get_member_best_name(member),
                    "discord_avatar_url": _get_member_avatar_url(member),
                    "has_discord_role": has_discord_role,
                })
                discord_identity_map[user_key] = identity

        sync_staff_archive_state(
            user_id=user_key,
            username=_existing_best_name(user_key),
            rank_name=None,
            has_vrc_staff_role=False,
            has_discord_staff_role=bool(has_discord_role),
        )

        if identity.get("status") != "archived":
            stored_entry = leaderboard_data.get("staff", {}).get(user_key, {})
            existing_rank = stored_entry.get("rank_name") if isinstance(stored_entry, dict) else None
            display_name = _choose_display_name(
                user_key,
                identity.get("vrchat_username"),
                identity.get("discord_username"),
                *(stored_entry.get(k) for k in ("vrchat_username", "discord_username", "name") if isinstance(stored_entry, dict))
            )
            _cache_best_name(user_key, display_name)

            write_payload = {
                "rank_name": existing_rank,
                "discord_id": identity.get("discord_id"),
                "discord_username": identity.get("discord_username"),
                "discord_avatar_url": identity.get("discord_avatar_url"),
                "vrchat_username": identity.get("vrchat_username") or (stored_entry.get("vrchat_username") if isinstance(stored_entry, dict) else None),
                "vrchat_avatar_url": identity.get("vrchat_avatar_url") or (stored_entry.get("vrchat_avatar_url") if isinstance(stored_entry, dict) else None),
            }
            _ensure_staff_entry("staff", user_key, display_name, **write_payload)
            _ensure_staff_entry("monthly", user_key, display_name, **write_payload)

    app_state.leaderboard_dirty = True
    save_leaderboard_data()

    return count
