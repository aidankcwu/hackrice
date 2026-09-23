"""FastAPI application factory."""

from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager
from urllib.parse import parse_qs

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from longevity.server.ingest import INGEST_PATH
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ..config import Settings, get_settings
from ..recap.routes import router as recap_router
from ..wearables.connect import attach_fitbit, attach_google_health
from ..wearables.fitbit_routes import router as fitbit_router, set_sync
from ..wearables.google_health_routes import router as google_health_router, set_sync as set_google_health_sync
from .routes import router
from .wiring import Pipeline, build_pipeline


# The provider redirects the browser here with no token. Safe to leave open: each
# callback 400s unless `state` is one its own /authorize (behind the token) issued,
# and that state holds the PKCE verifier (fitbit_routes.py:40-47,
# google_health_routes.py:31-36). Exact paths only.
_OAUTH_CALLBACKS = frozenset({
    f"{fitbit_router.prefix}/callback",
    f"{google_health_router.prefix}/callback",
})


def _guarded(path: str) -> bool:
    """/api/*, the glasses socket, and the frame/ingest debug routes (raw frames
    are addressed by sequential refs). /docs, /redoc, /openapi.json stay open."""

    if path in _OAUTH_CALLBACKS:
        return False
    return (path in ("/api", INGEST_PATH, "/frames", "/ingest/stats")
            or path.startswith(("/api/", "/frames/")))


def _presented_token(scope: Scope) -> str:
    """``Authorization: Bearer <t>``, else ``?token=<t>``, else ''.

    The query form exists because browsers cannot put a header on an ``<img>``
    (the dashboard's evidence thumbnails) or on a browser WebSocket.
    """

    for name, value in scope.get("headers", ()):
        if name == b"authorization":
            scheme, _, credentials = value.decode("latin-1").partition(" ")
            if scheme.lower() == "bearer":
                return credentials.strip()
    query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
    return (query.get("token") or [""])[0]


class BearerAuth:
    """The one API_TOKEN gate over every path ``_guarded`` names.

    Off when API_TOKEN is unset or blank, read per request from the environment
    like WEARABLE_INGEST_TOKEN (``routes._check_token``), so a LAN demo runs as
    before. That route's X-Ingest-Token check still applies on top of this one.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket") and _guarded(scope["path"]):
            expected = os.environ.get("API_TOKEN", "").strip()
            presented = _presented_token(scope)
            if expected and not hmac.compare_digest(presented.encode(), expected.encode()):
                if scope["type"] == "websocket":
                    # Close before accept: the handshake is refused (HTTP 403).
                    await send({"type": "websocket.close", "code": 1008,
                                "reason": "bad or missing API token"})
                    return
                response = JSONResponse({"detail": "bad or missing API token"},
                                        status_code=401,
                                        headers={"WWW-Authenticate": "Bearer"})
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_app(pipeline: Pipeline | None = None, *, settings: Settings | None = None,
               **build_kwargs) -> FastAPI:
    # Build before route registration so the real-capture routers can be mounted
    # ahead of B's simulation-only /frames route (Starlette resolves first match).
    active = pipeline
    if active is None:
        kwargs = {"source": "sim", "reasoner_mode": "fake", "speed": 1.0}
        kwargs.update(build_kwargs)
        active = build_pipeline(settings or get_settings(), **kwargs)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.pipeline = active
        await active.start()
        fitbit_sync, fitbit_task = attach_fitbit(active.db, active.clock)
        app.state.fitbit = fitbit_sync
        google_health_sync, google_health_task = attach_google_health(active.db, active.clock)
        app.state.google_health = google_health_sync
        try:
            yield
        finally:
            set_sync(None)
            set_google_health_sync(None)
            await active.stop()

    app = FastAPI(lifespan=lifespan)
    app.state.pipeline = active
    # Added before CORS so CORS wraps it: a preflight (never credentialed) is
    # answered without a token, and a 401 still carries the CORS headers.
    app.add_middleware(BearerAuth)
    config = settings or getattr(active, "settings", None)
    origins = getattr(config, "cors_origins", None) or "http://localhost:3000"
    app.add_middleware(CORSMiddleware,
                       allow_origins=[o.strip() for o in origins.split(",") if o.strip()],
                       allow_credentials=True, allow_methods=["*"],
                       allow_headers=["*"])
    if active.capture is not None:
        from longevity.server import frames as capture_frames, ingest
        app.state.glasses_link = active.capture.link
        app.state.frame_ring = active.capture.ring
        app.include_router(ingest.router)
        app.include_router(capture_frames.router)
    app.include_router(router)
    app.include_router(recap_router)
    app.include_router(fitbit_router)
    app.include_router(google_health_router)
    return app
