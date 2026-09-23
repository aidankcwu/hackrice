"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config import Settings, get_settings
from ..recap.routes import router as recap_router
from ..wearables.connect import attach_fitbit, attach_google_health
from ..wearables.fitbit_routes import router as fitbit_router, set_sync
from ..wearables.google_health_routes import router as google_health_router, set_sync as set_google_health_sync
from .auth import AccessTokenMiddleware
from .routes import router
from .wiring import Pipeline, build_pipeline


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

    settings_used = getattr(active, "settings", None)
    # No root_path here on purpose: behind the /t/NAME proxy it is uvicorn's
    # job (pipeline.main passes ROOT_PATH to it), because the ASGI server is
    # what puts the prefix into the path Starlette builds redirect URLs from.
    app = FastAPI(lifespan=lifespan)
    app.state.pipeline = active
    # Read by the glasses socket (longevity.server.ingest), which checks the
    # token itself so it can close with 4401 rather than fail the handshake.
    access_token = getattr(settings_used, "access_token", "") or ""
    app.state.access_token = access_token
    # Order matters: add_middleware puts the last one outermost. CORS must be
    # outside the token check, or a 401 would go out without the CORS headers
    # and the browser would report a CORS error instead of "wrong token".
    app.add_middleware(AccessTokenMiddleware, token=access_token)
    origins = (settings_used.cors_origin_list()
               if hasattr(settings_used, "cors_origin_list") else ["http://localhost:3000"])
    app.add_middleware(CORSMiddleware,
                       allow_origins=origins,
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
