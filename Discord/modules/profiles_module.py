"""ProfilesModule — keeps Discord role + archive state in sync.

The dashboard emits `staff.archived` and `staff.role_updated`.
This module ensures the Discord Guild reflects the Dashboard's source of truth.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, ClassVar, Optional

from core.base_module import BaseModule

log = logging.getLogger("bot_v2.profiles")


class ProfilesModule(BaseModule):
    name = "profiles"
    handled_events: ClassVar[set[str]] = {
        "staff.archived",
        "staff.role_updated",
    }

    # Roles the module considers "staff" — removed on archive, added
    # on role change. Subset of the dashboard's ROLE_HIERARCHY.
    _STAFF_ROLE_NAMES: ClassVar[tuple[str, ...]] = (
        "Owner", "Underboss", "Consigliere", "Capo", "Soldier",
    )

    def __init__(
        self,
        *,
        bot: Any = None,
        guild_id_getter: Callable[[], int],
        role_id_map_getter: Callable[[], dict[str, int]],
        archived_role_id_getter: Optional[Callable[[], Optional[int]]] = None,
    ):
        super().__init__(bot=bot)
        self._guild_id = guild_id_getter
        self._role_id_map = role_id_map_getter
        self._archived_role_id = archived_role_id_getter or (lambda: None)

    # ── Outbound ────────────────────────────────────────────────

    async def on_outbound(self, event_type: str, payload: dict) -> None:
        discord_id = str(payload.get("discord_id") or "").strip()
        if not discord_id or not discord_id.isdigit():
            log.debug("profiles: skipping %s — no/bad discord_id", event_type)
            return

        guild = self._resolve_guild()
        if not guild:
            log.warning("profiles: guild unavailable, skipping %s", event_type)
            return

        member = guild.get_member(int(discord_id))
        if member is None:
            log.info("profiles: member %s not in guild, skipping %s", discord_id, event_type)
            return

        if event_type == "staff.archived":
            await self._apply_archive(guild, member, bool(payload.get("archived")))
        elif event_type == "staff.role_updated":
            await self._apply_role_change(
                guild, member,
                new_role=payload.get("new_role") or "",
                old_role=payload.get("old_role") or "",
            )

    async def _apply_archive(self, guild: Any, member: Any, archived: bool) -> None:
        staff_role_ids = self._staff_role_ids()
        archived_id = self._archived_role_id()
        try:
            if archived:
                to_remove = [r for r in member.roles if r.id in staff_role_ids]
                if to_remove:
                    await member.remove_roles(*to_remove, reason="Dashboard: archived")
                
                if archived_id is not None:
                    arch_role = guild.get_role(archived_id)
                    if arch_role and arch_role not in member.roles:
                        await member.add_roles(arch_role, reason="Dashboard: archived")
            else:
                if archived_id is not None:
                    arch_role = guild.get_role(archived_id)
                    if arch_role and arch_role in member.roles:
                        await member.remove_roles(arch_role, reason="Dashboard: unarchived")
        except Exception:
            log.exception("profiles: apply_archive failed")
            raise 

    async def _apply_role_change(
        self, guild: Any, member: Any, *, new_role: str, old_role: str,
    ) -> None:
        role_map = self._role_id_map() or {}
        new_role_id = role_map.get(new_role)
        
        if new_role and new_role_id is None:
            log.warning("profiles: role %r not in map for member=%s", new_role, member.id)
            return

        try:
            staff_role_ids = self._staff_role_ids()
            # Converge to exactly one staff role: remove others
            to_remove = [r for r in member.roles if r.id in staff_role_ids and r.id != new_role_id]
            if to_remove:
                await member.remove_roles(*to_remove, reason=f"Dashboard: role → {new_role}")
            
            if new_role_id is not None:
                target = guild.get_role(new_role_id)
                if target and target not in member.roles:
                    await member.add_roles(target, reason=f"Dashboard: role → {new_role}")
        except Exception:
            log.exception("profiles: apply_role_change failed")
            raise

    def _staff_role_ids(self) -> set[int]:
        role_map = self._role_id_map() or {}
        return {role_map[name] for name in self._STAFF_ROLE_NAMES if name in role_map}

    def _resolve_guild(self) -> Any:
        if not self.bot: return None
        try:
            return self.bot.get_guild(int(self._guild_id()))
        except (TypeError, ValueError):
            return None

    # ── Reconcile (cold-start) ──────────────────────────────────

    async def reconcile(self) -> dict:
        corrected = 0
        errors = 0
        guild = self._resolve_guild()
        
        if not guild:
            return {"module": self.name, "corrected": 0, "errors": 0, "note": "guild unavailable"}

        try:
            staff_rows = await self._fetch_dashboard_staff()
        except Exception:
            log.exception("profiles.reconcile: dashboard fetch failed")
            return {"module": self.name, "corrected": 0, "errors": 1}

        role_map = self._role_id_map() or {}
        staff_role_ids = self._staff_role_ids()
        archived_id = self._archived_role_id()

        for row in staff_rows:
            discord_id = str(row.get("discord_id") or "").strip()
            if not discord_id.isdigit():
                continue
            
            member = guild.get_member(int(discord_id))
            if member is None:
                continue

            try:
                if row.get("archived"):
                    # Strip staff roles, ensure archived role
                    to_remove = [r for r in member.roles if r.id in staff_role_ids]
                    if to_remove:
                        await member.remove_roles(*to_remove, reason="reconcile: archived")
                        corrected += 1
                    if archived_id is not None:
                        arch = guild.get_role(archived_id)
                        if arch and arch not in member.roles:
                            await member.add_roles(arch, reason="reconcile: archived")
                            corrected += 1
                else:
                    # Ensure correct role, strip others
                    target_role_id = role_map.get(row.get("role") or "")
                    if target_role_id is None:
                        continue
                    
                    to_remove = [r for r in member.roles if r.id in staff_role_ids and r.id != target_role_id]
                    if to_remove:
                        await member.remove_roles(*to_remove, reason=f"reconcile: role → {row.get('role')}")
                        corrected += 1
                    
                    target = guild.get_role(target_role_id)
                    if target and target not in member.roles:
                        await member.add_roles(target, reason=f"reconcile: role → {row.get('role')}")
                        corrected += 1
            except Exception:
                log.exception("profiles.reconcile: member=%s failed", discord_id)
                errors += 1

        return {
            "module": self.name,
            "corrected": corrected,
            "errors": errors,
            "staff_processed": len(staff_rows),
        }

    async def _fetch_dashboard_staff(self) -> list[dict]:
        """Tries bot-optimized endpoint first, falls back to standard staff list."""
        session = await self.sync._get_session()
        for path in ("/staff/bot-roster", "/staff"):
            url = f"{self.sync.base_url}{path}"
            try:
                async with session.get(url, params={"include_archived": "true"}) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if isinstance(data, dict):
                            return data.get("items") or data.get("staff") or []
                        return data or []
            except Exception as e:
                log.debug("fetch failed for %s: %r", path, e)
                continue
        return []
