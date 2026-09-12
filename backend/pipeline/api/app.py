"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config import Settings, get_settings
from ..wearables.connect import attach_fitbit
from ..wearables.fitbit_routes import router as fitbit_router, set_sync
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
        try:
            yield
        finally:
            set_sync(None)
            await active.stop()

    app = FastAPI(lifespan=lifespan)
    app.state.pipeline = active
    app.add_middleware(CORSMiddleware,
                       allow_origins=["http://localhost:3000"],
                       allow_credentials=True, allow_methods=["*"],
                       allow_headers=["*"])
    if active.capture is not None:
        from longevity.server import frames as capture_frames, ingest
        app.state.glasses_link = active.capture.link
        app.state.frame_ring = active.capture.ring
        app.include_router(ingest.router)
        app.include_router(capture_frames.router)
    app.include_router(router)
    app.include_router(fitbit_router)
    return app
