"""Patch for Discord/services/leaderboard/staff_sync.py

Two targeted changes that together fix Choso's missing avatar and make
identity-matching robust against stylized Unicode display names.

This file is a PATCH SNIPPET — not a runnable module. The two functions
below are meant to REPLACE the identically-named functions in your
existing staff_sync.py. Every helper they call (`_get_bot_guild`,
`_iter_known_staff_entries`, `_extract_*`, `_has_staff_role`,
`_get_member_best_name`, `_get_member_avatar_url`) already exists in
that file and stays unchanged — that's why ruff/pyright flagging them
as "undefined" here is expected. We disable that check below.

Apply:
  1. REPLACE `_normalize_name_key()` with the version below.
  2. REPLACE `_get_discord_identity_map()` with the version below.

Keep everything else in staff_sync.py exactly as-is. No imports change
except `unicodedata` (stdlib, no pip install).

─── WHAT CHOSO'S BUG LOOKS LIKE ──────────────────────────────────────────

`ℂ𝕙𝕠𝕤𝕠` uses Mathematical Double-Struck letters (U+2102, U+210D,
U+1D560, U+1D564, U+1D560). The old normalizer:

    text = "ℂ𝕙𝕠𝕤𝕠".strip().lower()   # still "ℂ𝕙𝕠𝕤𝕠" — no lowercase mapping
    "".join(ch for ch in text if ch.isalnum())  # "ℂ𝕙𝕠𝕤𝕠" — alnum returns True

So the VRChat-side key is "ℂ𝕙𝕠𝕤𝕠" while the Discord-side key is
"choso". `by_name_key.get(...)` therefore misses and Choso's `discord_id`
never gets stamped on the leaderboard row → autosave skips the Discord
avatar fetch → dashboard shows the letter-fallback placeholder.

─── FIX #1: NFKC normalization before lowercase ─────────────────────────

`unicodedata.normalize("NFKC", ...)` maps the Mathematical Alphanumeric
Symbols block to their canonical ASCII equivalents:

    >>> import unicodedata
    >>> unicodedata.normalize("NFKC", "ℂ𝕙𝕠𝕤𝕠").lower()
    'choso'

Free, pure-stdlib, no performance impact. Also fixes any future staff
using superscript/subscript/small-caps/full-width variants.

─── FIX #2: seed the identity map from STAFF_ALERT_ORDER ─────────────────

The hardcoded `STAFF_ALERT_ORDER` in `core/config.py` is the
authoritative source of `{discord_id, vrchat_user_id}` pairs — it's
maintained by hand, so whatever's in there is verified. We use it as a
pre-seed so identity matching never depends on fuzzy name matching when
it doesn't have to. Fixes any future staff whose Discord and VRChat
display names differ (nicknames, joke handles, renames, etc.).
"""
# ruff: noqa: F821   # helpers are defined in the parent staff_sync.py
from __future__ import annotations

import unicodedata
from typing import Dict, List, Optional

from core.cache import app_state
from core.config import GUILD_ID, STAFF_ALERT_ORDER, VRC_STAFF_ROLE_NAMES


# ─────────────────────────────────────────────────────────────────────
# FIX #1 — drop-in replacement for _normalize_name_key
# ─────────────────────────────────────────────────────────────────────

def _normalize_name_key(value: str | None) -> str:
    """
    Normalize names for fuzzy-safe exact matching.

    Uses Unicode NFKC compatibility normalization BEFORE stripping to
    non-alphanumerics. This folds stylized characters (mathematical
    bold/italic/double-struck, full-width, superscript, etc.) to their
    canonical equivalents, so:

        SoloK1lls, solo_k1lls, solo-k1lls       → "solok1lls"
        Choso, 𝗖𝗵𝗼𝘀𝗼, ℂ𝕙𝕠𝕤𝕠, Ｃｈｏｓｏ        → "choso"

    All stay identical under the comparison.
    """
    text = str(value or "")
    # NFKC folds compatibility characters to canonical form.
    #   ℂ (U+2102) → C
    #   𝕙 (U+1D559) → h
    #   Ｃｈｏｓｏ (fullwidth) → Choso
    text = unicodedata.normalize("NFKC", text)
    text = text.strip().lower()
    return "".join(ch for ch in text if ch.isalnum())


# ─────────────────────────────────────────────────────────────────────
# FIX #2 — drop-in replacement for _get_discord_identity_map
# ─────────────────────────────────────────────────────────────────────
#
# ONLY the `all_candidates` construction changes — we prepend entries
# derived from STAFF_ALERT_ORDER so every configured staffer has an
# authoritative {vrchat_user_id, discord_id} seed in the map before any
# name-based fallback runs. The rest of the function is unchanged from
# the original.

def _staff_alert_order_pairs() -> list[dict]:
    """Flatten STAFF_ALERT_ORDER into a list of authoritative identity
    seeds. Deduplicates because the same staffer can appear under
    multiple action buckets (warn/kick/ban)."""
    seen: set[tuple[str, str]] = set()
    pairs: list[dict] = []
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
                pairs.append({
                    "user_id": vrc_id,           # picked up by _extract_vrchat_user_id
                    "discord_id": discord_id,    # picked up by _extract_discord_id
                })
    return pairs


def _get_discord_identity_map(staff_members: List[dict]) -> Dict[str, dict]:
    """
    Build a mapping of vrchat_user_id -> discord identity info.

    Priority (highest to lowest):
      1. STAFF_ALERT_ORDER hardcoded pairs (authoritative, never wrong)
      2. discord_id present in live VRChat staff payload
      3. Cached VRC staff payload
      4. Existing stored entry's discord_id in leaderboard_data
      5. Name-based match against staff-role Discord members
    """
    result: Dict[str, dict] = {}
    guild = _get_bot_guild()

    all_candidates: List[dict] = []
    # FIX #2: seed from STAFF_ALERT_ORDER FIRST so the authoritative
    # pairs are inserted into `result` before any later source can
    # accidentally stomp them with a None.
    all_candidates.extend(_staff_alert_order_pairs())
    all_candidates.extend([m for m in staff_members if isinstance(m, dict)])

    cached_vrc_staff = getattr(app_state, "vrc_staff", None)
    if isinstance(cached_vrc_staff, list):
        all_candidates.extend([m for m in cached_vrc_staff if isinstance(m, dict)])

    for user_id, entry in _iter_known_staff_entries():
        all_candidates.append(
            {
                "user_id": user_id,
                "discord_id": entry.get("discord_id"),
                "vrchat_username": entry.get("vrchat_username") or entry.get("name"),
                "vrchat_avatar_url": entry.get("vrchat_avatar_url"),
            }
        )

    for member in all_candidates:
        vrchat_user_id = _extract_vrchat_user_id(member)
        if not vrchat_user_id:
            continue

        discord_id = _extract_discord_id(member)
        vrchat_name = _extract_vrchat_display_name(member)
        vrchat_avatar_url = _extract_vrchat_avatar_url(member)

        if vrchat_user_id not in result:
            result[vrchat_user_id] = {
                "discord_id": None,
                "discord_username": None,
                "has_discord_role": None,
                "discord_avatar_url": None,
                "vrchat_avatar_url": vrchat_avatar_url,
                "vrchat_username": vrchat_name,
            }

        if vrchat_avatar_url and not result[vrchat_user_id].get("vrchat_avatar_url"):
            result[vrchat_user_id]["vrchat_avatar_url"] = vrchat_avatar_url

        if vrchat_name and not result[vrchat_user_id].get("vrchat_username"):
            result[vrchat_user_id]["vrchat_username"] = vrchat_name

        # Only set discord_id if it's currently missing OR the incoming
        # value is from STAFF_ALERT_ORDER. The `not result[...].discord_id`
        # guard already handles this — STAFF_ALERT_ORDER runs first so it
        # always wins when present.
        if discord_id and not result[vrchat_user_id].get("discord_id"):
            result[vrchat_user_id]["discord_id"] = discord_id

    if guild is None:
        return result

    by_id: Dict[str, object] = {}
    by_name_key: Dict[str, object] = {}

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

    # Primary pass: if we have a discord_id (from STAFF_ALERT_ORDER or
    # elsewhere), resolve the guild member + avatar.
    for vrchat_user_id, identity in result.items():
        discord_id = str(identity.get("discord_id") or "").strip()
        if not discord_id:
            continue

        member = by_id.get(discord_id)
        if member is None:
            # Known discord_id but bot can't see member → fill has_role
            # flag False so archive logic still runs correctly. Avatar
            # stays None; autosave's presence snapshot will re-try on
            # every tick so a delayed guild.chunk() call eventually
            # populates it.
            identity["has_discord_role"] = False
            continue

        identity["has_discord_role"] = True
        identity["discord_username"] = _get_member_best_name(member)
        identity["discord_avatar_url"] = _get_member_avatar_url(member)

    # Fallback pass: for any identity STILL without a discord_id (i.e.
    # they're in the VRChat group but not in STAFF_ALERT_ORDER), try
    # name-based matching. Now correctly handles stylized Unicode because
    # _normalize_name_key uses NFKC.
    for vrchat_user_id, identity in result.items():
        if identity.get("discord_id"):
            continue

        vrchat_name = identity.get("vrchat_username")
        name_key = _normalize_name_key(vrchat_name)
        if not name_key:
            continue

        member = by_name_key.get(name_key)
        if member is None:
            continue

        discord_id = str(getattr(member, "id", "")).strip()
        if not discord_id:
            continue

        identity["discord_id"] = discord_id
        identity["discord_username"] = _get_member_best_name(member)
        identity["discord_avatar_url"] = _get_member_avatar_url(member)
        identity["has_discord_role"] = True

    return result


# ─────────────────────────────────────────────────────────────────────
# NOTE: The following helpers are referenced above and already exist
# in your staff_sync.py unchanged. Listed here only to document the
# surface area. Do NOT copy these — just keep what you already have:
#
#   _get_bot_guild
#   _iter_known_staff_entries
#   _extract_vrchat_user_id
#   _extract_discord_id
#   _extract_vrchat_display_name
#   _extract_vrchat_avatar_url
#   _has_staff_role
#   _get_member_best_name
#   _get_member_avatar_url
# ─────────────────────────────────────────────────────────────────────
