"""Attach live wearable pollers to a running app (SPEC §15).

The Fitbit poller only produces canonical payloads; this module owns the
sink that turns them into rows: intraday ``samples`` go through the generic
ingest path (``origin="live"``), ``daily`` rows go to the ``seeded`` table
with the real device as ``source``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from ..db import Database
from ..models import SeededRow
from .fitbit import FitbitClient, FitbitConfig, FitbitSync, TokenStore
from .fitbit_routes import set_sync, start_polling
from .ingest import ingest

log = logging.getLogger(__name__)


def make_sink(db: Database, clock: Any | None = None):
    def sink(payload: dict[str, Any]) -> dict[str, Any]:
        result = {"samples": None, "daily": 0}
        samples = payload.get("samples") or []
        if samples:
            result["samples"] = ingest(
                db, {"device": payload.get("device", "fitbit"), "samples": samples},
                wall_to_tick=clock.wall_to_tick if clock is not None else None,
            )
        daily = payload.get("daily") or []
        if daily:
            rows = [
                SeededRow(
                    day=str(r["day"]), metric=str(r["metric"]), value=float(r["value"]),
                    unit=str(r.get("unit", "")), source=str(r.get("source", payload.get("device", "fitbit"))),
                )
                for r in daily
                if r.get("value") is not None
            ]
            result["daily"] = db.insert_seeded_rows(rows)
        log.info("wearable sink: %s", result)
        return result

    return sink


def attach_fitbit(db: Database, clock: Any | None = None) -> tuple[FitbitSync | None, asyncio.Task | None]:
    """Build the Fitbit poller if credentials exist; start it if a token exists.

    Returns ``(sync, task)``. ``sync`` is registered with the routes so
    ``/api/wearables/fitbit/authorize`` works even before the first token;
    ``task`` is the ``run_forever`` loop (None until authorised — the
    callback route can trigger a sync on demand).
    """

    config = FitbitConfig.from_env()
    if not config.client_id:
        set_sync(None)
        return None, None
    client = FitbitClient(config, TokenStore(config.token_path))
    sync = FitbitSync(client, make_sink(db, clock), interval_s=config.poll_s)
    set_sync(sync)
    task = None
    if client.store.is_configured():
        task = start_polling(sync)
        log.info("fitbit: poller started (every %ss)", config.poll_s)
    else:
        log.info("fitbit: configured but not authorised — open /api/wearables/fitbit/authorize")
    return sync, task
