"""Mountable FastAPI routes for Fitbit OAuth and manual synchronization."""
from __future__ import annotations

import secrets
import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from .fitbit import FitbitSync

router = APIRouter(prefix="/api/wearables/fitbit", tags=["fitbit"])
_sync: FitbitSync | None = None
_pending: dict[str, str] = {}
_poll_task: asyncio.Task[None] | None = None
log = logging.getLogger(__name__)

def set_sync(sync: FitbitSync | None) -> None:
    global _sync, _poll_task
    _sync = sync
    if sync is None and _poll_task is not None:
        _poll_task.cancel()
        _poll_task = None

def start_polling(sync: FitbitSync) -> asyncio.Task[None]:
    """Start one poll loop only; called after OAuth and app startup."""
    global _poll_task
    if _poll_task is None or _poll_task.done():
        _poll_task = asyncio.create_task(sync.run_forever(), name="fitbit-sync")
    return _poll_task

def _require() -> FitbitSync:
    if _sync is None: raise HTTPException(503, "Fitbit integration is not configured")
    return _sync

@router.get("/authorize")
async def authorize() -> RedirectResponse:
    sync = _require(); state = secrets.token_urlsafe(24)
    url, verifier = sync.client.authorize_url(state); _pending[state] = verifier
    return RedirectResponse(url, status_code=307)

@router.get("/callback", response_class=HTMLResponse)
async def callback(code: str = Query(...), state: str = Query(...)) -> HTMLResponse:
    sync = _require(); verifier = _pending.pop(state, None)
    if verifier is None: raise HTTPException(400, "Invalid or expired Fitbit OAuth state")
    await sync.client.exchange_code(code, verifier); sync.connected = True

    # run_forever() syncs immediately on its first iteration, so a separate
    # one-off sync here would double every Fitbit request (Astra review).
    start_polling(sync)
    return HTMLResponse("<!doctype html><title>Fitbit connected</title><p>Fitbit connected — you can close this tab</p>")

@router.get("/status")
async def status() -> dict[str, Any]:
    if _sync is None:
        return {"configured": False, "connected": False, "last_sync_t": None, "last_error": None,
                "token_expires_at": None, "requests_last_hour": 0, "polling": False}
    token = _sync.client.store.load() or {}
    return {"configured": bool(_sync.client.config.client_id and _sync.client.config.client_secret),
            "connected": _sync.connected, "last_sync_t": _sync.last_sync_t, "last_error": _sync.last_error,
            "token_expires_at": token.get("expires_at"), "requests_last_hour": _sync.requests_last_hour,
            "polling": _poll_task is not None and not _poll_task.done()}

@router.post("/sync")
async def sync_now() -> dict[str, int]: return await _require().sync_once()
