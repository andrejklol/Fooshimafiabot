"""ReflectionLayer — WebSocket + polling fallback.

Dashboard is the source of truth. This class is the ONLY component
that receives outbound events and hands them to the registry for
dispatch. Two channels:

  1. WebSocket `/ws/sync/outbound-events` — sub-second latency.
     Auto-reconnects with exponential back-off on disconnect.
  2. Poll `/api/sync/outbound-events?since=…` — 60s interval,
     runs concurrently with the WS so an outage of either doesn't
     halt event flow.

Both channels feed the same `registry.process_event()` — dedupe
handles the overlap.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

import aiohttp

log = logging.getLogger("bot_v2.reflection")


@dataclass
class ReflectionLayer:
    dashboard_sync: Any                   # DashboardSync with .base_url + ._get_session()
    registry: Any                         # UnifiedEventRegistry
    bot_id: str = "vrc-bot-unnamed"
    poll_interval_seconds: float = 60.0
    ws_path: str = "/ws/sync/outbound-events"
    poll_path: str = "/sync/outbound-events"
    ack_path: str = "/sync/outbound-events/ack"
    initial_lookback_seconds: int = 300   # 5 min replay on restart
    ack_batch_size: int = 50
    ws_backoff_max_seconds: float = 60.0

    _poll_task: Optional[asyncio.Task] = field(default=None, init=False, repr=False)
    _ws_task: Optional[asyncio.Task] = field(default=None, init=False, repr=False)
    _stop: asyncio.Event = field(default_factory=asyncio.Event, init=False, repr=False)
    _cursor: Optional[str] = field(default=None, init=False, repr=False)

    # ── Lifecycle ───────────────────────────────────────────────

    async def start(self) -> None:
        if self._poll_task and not self._poll_task.done():
            log.warning("ReflectionLayer.start() called twice — no-op")
            return
        
        self._stop.clear()
        self._cursor = (
            datetime.now(timezone.utc)
            - timedelta(seconds=self.initial_lookback_seconds)
        ).isoformat()
        
        # Start the mandatory poll loop
        self._poll_task = asyncio.create_task(self._poll_loop())

        # WebSocket is an optimization. Disable it via env if Cloudflare 
        # is blocking non-browser User-Agents with 403s.
        ws_enabled = os.getenv("DASHBOARD_WS_ENABLED", "true").lower() not in ("false", "0", "no")
        
        if ws_enabled:
            self._ws_task = asyncio.create_task(self._ws_loop())
        else:
            log.info("ReflectionLayer: WebSocket disabled via DASHBOARD_WS_ENABLED=false — poll-only mode")

        log.info(
            "ReflectionLayer started (bot_id=%s cursor=%s poll=%.1fs ws_enabled=%s)",
            self.bot_id, self._cursor, self.poll_interval_seconds, ws_enabled
        )

    async def stop(self) -> None:
        self._stop.set()
        # Clean up tasks if they were actually started
        tasks = [t for t in (self._poll_task, self._ws_task) if t is not None]
        
        if tasks:
            for task in tasks:
                try:
                    await asyncio.wait_for(task, timeout=5)
                except asyncio.TimeoutError:
                    task.cancel()
                except Exception:
                    pass
        
        self._poll_task = None
        self._ws_task = None
        log.info("ReflectionLayer stopped")

    # ── Poll loop (durable path) ────────────────────────────────

    async def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._drain_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("poll drain failed — retrying")
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.poll_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass

    async def _drain_once(self) -> int:
        session = await self.dashboard_sync._get_session()
        url = f"{self.dashboard_sync.base_url}{self.poll_path}"
        params = {"since": self._cursor or "", "limit": 50}
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                body = (await resp.text())[:200]
                log.warning("poll returned %s: %s", resp.status, body)
                return 0
            data = await resp.json()

        events = data.get("events") or []
        if not events:
            self._cursor = data.get("next_cursor") or self._cursor
            return 0

        to_ack: list[str] = []
        for event in events:
            should_ack = await self.registry.process_event(event)
            if should_ack and event.get("id"):
                to_ack.append(event["id"])
            
            # Update cursor to the timestamp of the last processed event
            self._cursor = event.get("created_at") or self._cursor

        if to_ack:
            await self._ack(to_ack)
        return len(events)

    async def _ack(self, event_ids: list[str]) -> None:
        session = await self.dashboard_sync._get_session()
        url = f"{self.dashboard_sync.base_url}{self.ack_path}"
        for i in range(0, len(event_ids), self.ack_batch_size):
            chunk = event_ids[i:i + self.ack_batch_size]
            try:
                async with session.post(
                    url,
                    json={"bot_id": self.bot_id, "event_ids": chunk},
                ) as resp:
                    if resp.status != 200:
                        body = (await resp.text())[:200]
                        log.warning("ack returned %s: %s", resp.status, body)
            except aiohttp.ClientError:
                log.exception("ack network error — retry next poll")

    # ── WebSocket loop (low-latency path) ───────────────────────

    async def _ws_loop(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                await self._connect_ws()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("ws error: %r — reconnecting in %.1fs", exc, backoff)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(self.ws_backoff_max_seconds, backoff * 2)

    async def _connect_ws(self) -> None:
        session = await self.dashboard_sync._get_session()
        base = self.dashboard_sync.base_url
        if base.startswith("https://"):
            ws_base = "wss://" + base[len("https://"):]
        elif base.startswith("http://"):
            ws_base = "ws://" + base[len("http://"):]
        else:
            ws_base = base
        
        url = f"{ws_base}{self.ws_path}"
        log.info("opening WebSocket %s", url)
        async with session.ws_connect(url, heartbeat=30) as ws:
            async for msg in ws:
                if self._stop.is_set():
                    break
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_ws_frame(msg.data)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    raise RuntimeError(f"ws closed/error: {msg.data!r}")

    async def _handle_ws_frame(self, frame: str) -> None:
        try:
            parsed = json.loads(frame)
        except json.JSONDecodeError:
            log.warning("ws non-json frame: %r", frame[:120])
            return
        
        if parsed.get("kind") != "event":
            return  # hello / echo / heartbeat
            
        event = parsed.get("event") or {}
        should_ack = await self.registry.process_event(event)
        if should_ack and event.get("id"):
            await self._ack([event["id"]])
