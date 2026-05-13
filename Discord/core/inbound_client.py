from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any, Optional, Callable

import aiohttp

log = logging.getLogger("bot_v2.inbound_client")


class InboundEventClient:
    _RETRY_STATUSES = {502, 503, 504, 520, 522, 524}
    _RETRY_DELAYS = (1.0, 3.0, 9.0)

    def __init__(
        self,
        *,
        dashboard_sync: Any,
        actor_resolver: Optional[Callable[[], str]] = None,
    ):
        self.sync = dashboard_sync
        self._actor_resolver = actor_resolver or (lambda: "bot")

    # ─────────────────────────────────────────────
    # IDEMPOTENCY KEY GENERATION (HARDENED)
    # ─────────────────────────────────────────────
    @staticmethod
    def _derive_idempotency_key(
        event_type: str,
        payload: dict,
        *,
        actor: str | None = None,
    ) -> str:
        """
        Stable cross-system idempotency key.

        Now includes:
        - event_type
        - payload
        - actor (optional but stabilizes multi-bot environments)
        """
        serialised = json.dumps(
            {
                "t": event_type,
                "p": payload,
                "a": actor,
            },
            sort_keys=True,
            default=str,
        )

        digest = hashlib.sha256(serialised.encode("utf-8")).hexdigest()
        return f"{event_type}:{digest[:16]}"

    async def emit(
        self,
        event_type: str,
        payload: dict,
        *,
        idempotency_key: Optional[str] = None,
        actor: Optional[str] = None,
    ) -> dict | None:
        """
        POST one event to dashboard inbound pipeline.
        """

        session = await self.sync._get_session()
        url = f"{self.sync.base_url}/events/inbound"

        # ─────────────────────────────────────────────
        # EVENT NORMALIZATION (NEW)
        # ─────────────────────────────────────────────
        stable_actor = actor or self._actor_resolver()

        # allow upstream event identity passthrough
        upstream_id = (
            payload.get("event_id")
            or payload.get("external_id")
            or payload.get("id")
        )

        key = idempotency_key or self._derive_idempotency_key(
            event_type,
            payload,
            actor=stable_actor,
        )

        if upstream_id:
            # strengthens cross-layer dedupe consistency
            key = f"{key}:{upstream_id}"

        body = {
            "event_type": event_type,
            "payload": payload,
            "idempotency_key": key,
            "actor": stable_actor,
        }

        last_exc: Exception | None = None

        for attempt, delay in enumerate([0.0, *self._RETRY_DELAYS]):
            if delay:
                await asyncio.sleep(delay)

            try:
                async with session.post(url, json=body) as resp:
                    if resp.status == 200:
                        data = await resp.json()

                        log.debug(
                            "emit ok type=%s key=%s duplicate=%s",
                            event_type,
                            key,
                            data.get("duplicate"),
                        )
                        return data

                    if resp.status in self._RETRY_STATUSES:
                        text = (await resp.text())[:200]
                        log.warning(
                            "emit retry type=%s attempt=%d status=%d body=%s",
                            event_type,
                            attempt + 1,
                            resp.status,
                            text,
                        )
                        continue

                    text = (await resp.text())[:200]
                    log.error(
                        "emit rejected type=%s status=%d body=%s",
                        event_type,
                        resp.status,
                        text,
                    )
                    return None

            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_exc = exc
                log.warning(
                    "emit network error type=%s attempt=%d err=%r",
                    event_type,
                    attempt + 1,
                    exc,
                )
                continue

        log.error(
            "emit exhausted retries type=%s last_exc=%r",
            event_type,
            last_exc,
        )
        return None
