from __future__ import annotations
import json
import logging
import os
import asyncio
import time
from aiohttp import web
import discord
from discord.ext import commands, tasks

from core.cache import app_state
from core.config import AUTOSAVE_SECONDS, LOG_POLL_MINUTES
from services.tasks.autosave import autosave_if_dirty
from services.tasks.group_cache import refresh_group_cache_once
from services.tasks.log_polling import check_logs_once
from services.tasks.monthly_reset import check_monthly_reset, initialize_monthly_reset_state

log = logging.getLogger("tasks")

# Reduced default from 15 to 2 to prevent "stuck" stats
GROUP_CACHE_REFRESH_MINUTES = int(os.getenv("GROUP_CACHE_REFRESH_MINUTES", "2"))
MONTHLY_RESET_CHECK_MINUTES = int(os.getenv("MONTHLY_RESET_CHECK_MINUTES", "1"))
WEB_SERVER_PORT = int(os.getenv("BOT_WEB_PORT", "5043"))
AUTH_SECRET = os.getenv("DASHBOARD_WEBHOOK_SECRET")

class TasksCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        app_state.bot = bot
        
        self.cog_load_time = time.time()
        self.processed_tickets: dict[str, float] = {}
        
        # Start core loops
        self.autosave_loop.start()
        self.check_logs_loop.start()
        self.refresh_group_cache_loop.start()
        self.monthly_reset_loop.start()
        
        # New Heartbeat loop to prevent "Falling Asleep"
        self.heartbeat_sync_loop.start()
        
        self.bot.loop.create_task(self.start_web_server())

    def cog_unload(self):
        self.autosave_loop.cancel()
        self.check_logs_loop.cancel()
        self.refresh_group_cache_loop.cancel()
        self.monthly_reset_loop.cancel()
        self.heartbeat_sync_loop.cancel()

    async def start_web_server(self):
        if not AUTH_SECRET:
            log.critical("Dashboard API Bridge aborting: DASHBOARD_WEBHOOK_SECRET env var is unset!")
            return
        app = web.Application()
        app.add_routes([web.post('/api/dashboard-reply', self.handle_dashboard_reply)])
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, '0.0.0.0', WEB_SERVER_PORT).start()
        log.info("Dashboard API Bridge online on port %s (auth: shared-secret)", WEB_SERVER_PORT)

    @tasks.loop(minutes=5)
    async def heartbeat_sync_loop(self):
        """Forces a hard sync every 5 mins to fix 'ghost' online statuses."""
        try:
            await refresh_group_cache_once(force=True)
            log.info("Heartbeat: Dashboard sync forced to prevent stale session.")
        except Exception:
            log.warning("Heartbeat sync failed - checking connection...")

    @heartbeat_sync_loop.before_loop
    async def before_heartbeat_loop(self):
        await self.bot.wait_until_ready()

    async def handle_dashboard_reply(self, request: web.Request):
        provided_secret = request.headers.get("X-Dashboard-Secret")
        if not provided_secret or provided_secret != AUTH_SECRET:
            log.warning("Unauthorized dashboard payload attempt blocked.")
            return web.Response(text=json.dumps({"status": "unauthorized"}), content_type="application/json", status=401)

        try:
            d = await request.json()
            st = str(d.get("sender_type", "staff")).lower()
            rcid = d.get("client_user_id") or d.get("user_id")
            rsid, rsids = d.get("staff_user_id"), d.get("staff_ids", [])
            sname, cname = d.get("staff_name", "Staff"), d.get("client_name", "Client")
            msg, url, tid = d.get("message_preview", "..."), d.get("dashboard_url", "https://fooshimafia.net"), d.get("ticket_id", "N/A")
            severity = d.get("severity")

            # Reduced from 10.0 to 3.0 to catch events sooner after restart
            if time.time() - self.cog_load_time < 3.0:
                log.info("Initialization safety: Ignored %s", tid)
                return web.Response(text=json.dumps({"status": "success"}), content_type="application/json", status=200)

            now = asyncio.get_event_loop().time()
            self.processed_tickets = {k: v for k, v in self.processed_tickets.items() if now - v < 15.0}

            dedup_key = f"{st}_{tid}_{rcid}_{rsid}"
            if tid != "N/A" and dedup_key in self.processed_tickets:
                return web.Response(text=json.dumps({"status": "duplicate"}), content_type="application/json", status=200)

            if not self.bot.guilds:
                return web.Response(text=json.dumps({"status": "error"}), content_type="application/json", status=503)
            
            guild = self.bot.guilds[0]
            txt = msg[:150] + "..." if len(msg) > 150 else msg

            # STAFF REPLIED
            if st == "staff":
                if not rcid or not str(rcid).isdigit(): return web.Response(status=200)
                m = guild.get_member(int(rcid)) or await guild.fetch_member(int(rcid))
                if m:
                    if tid != "N/A": self.processed_tickets[dedup_key] = now
                    e = discord.Embed(title="🎩 Support Update", description=f"Staff (**{sname}**) responded!", color=0x800080)
                    e.add_field(name="Preview", value=f"*{txt}*").add_field(name="Link", value=f"[Open Dashboard]({url})")
                    e.set_footer(text=f"Ticket ID: {tid}")
                    await m.send(embed=e)
                return web.Response(status=200)

            # CLIENT REPLIED
            elif st == "client":
                targets = {int(rsid)} if (rsid and str(rsid).isdigit()) else set()
                if rsids: targets.update([int(i) for i in rsids if str(i).isdigit()])
                if tid != "N/A": self.processed_tickets[dedup_key] = now

                for sid in targets:
                    try:
                        m = guild.get_member(sid) or await guild.fetch_member(sid)
                        if m:
                            e = discord.Embed(title="🚨 Ticket Reply Waiting", description=f"Client **{cname}** responded!", color=0xFFD700)
                            e.add_field(name="Preview", value=f"*{txt}*").add_field(name="Link", value=f"[View Ticket]({url})")
                            e.set_footer(text=f"Ticket ID: {tid}")
                            await m.send(embed=e)
                            await asyncio.sleep(0.2)
                    except Exception: pass
                return web.Response(status=200)

            # NEW TICKET
            elif st in ("new_ticket", "new_ticket_dm"):
                staff_payload = d.get("severity_staff_ids") or d.get("all_staff_ids") or d.get("staff_ids", [])
                valid_staff = [int(sid) for sid in staff_payload if str(sid).isdigit()]
                if tid != "N/A": self.processed_tickets[dedup_key] = now

                color = 0xFF0000 if severity else 0x00FF00
                title = f"🚨 Severity Alert ({severity})" if severity else "🎟️ New Ticket"
                e = discord.Embed(title=title, description=f"Client **{cname}** opened a ticket.", color=color)
                e.add_field(name="Summary", value=f"*{txt}*").add_field(name="Link", value=f"[Review Portal]({url})")
                e.set_footer(text=f"Ticket ID: {tid}")
                
                for sid in valid_staff:
                    try:
                        m = guild.get_member(sid) or await guild.fetch_member(sid)
                        if m: 
                            await m.send(embed=e)
                            await asyncio.sleep(0.25)
                    except Exception: pass
                return web.Response(status=200)

            return web.Response(status=200)

        except Exception as err:
            log.exception("Web route error: %s", err)
            return web.Response(status=500)

    @tasks.loop(seconds=AUTOSAVE_SECONDS)
    async def autosave_loop(self):
        try: await autosave_if_dirty()
        except Exception: log.exception("Autosave error")

    @autosave_loop.before_loop
    async def before_autosave_loop(self): await self.bot.wait_until_ready()

    @tasks.loop(minutes=LOG_POLL_MINUTES)
    async def check_logs_loop(self):
        try: await check_logs_once()
        except Exception: log.exception("Log loop error")

    @check_logs_loop.before_loop
    async def before_check_logs_loop(self): await self.bot.wait_until_ready()

    @tasks.loop(minutes=GROUP_CACHE_REFRESH_MINUTES)
    async def refresh_group_cache_loop(self):
        try: await refresh_group_cache_once()
        except Exception: log.exception("Cache loop error")

    @refresh_group_cache_loop.before_loop
    async def before_refresh_group_cache_loop(self):
        await self.bot.wait_until_ready()
        try: await refresh_group_cache_once(force=True)
        except Exception: pass

    @tasks.loop(minutes=MONTHLY_RESET_CHECK_MINUTES)
    async def monthly_reset_loop(self):
        try: await check_monthly_reset()
        except Exception: log.exception("Reset loop error")

    @monthly_reset_loop.before_loop
    async def before_monthly_reset_loop(self):
        await self.bot.wait_until_ready()
        try: initialize_monthly_reset_state()
        except Exception: pass

async def setup(bot: commands.Bot):
    await bot.add_cog(TasksCog(bot))
