"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config import Settings, get_settings
from ..wearables.connect import attach_fitbit
from ..wearables.fitbit_routes import router as fitbit_router
from .routes import router
from .wiring import Pipeline, build_pipeline


def create_app(pipeline: Pipeline | None = None, *, settings: Settings | None = None,
               **build_kwargs) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        active = pipeline
        if active is None:
            # CLI-equivalent defaults so `uvicorn pipeline.api.app:create_app
            # --factory` works with no arguments (Astra review of S5a).
            kwargs = {"source": "sim", "reasoner_mode": "fake", "speed": 1.0}
            kwargs.update(build_kwargs)
            active = build_pipeline(settings or get_settings(), **kwargs)
        app.state.pipeline = active
        await active.start()
        fitbit_sync, fitbit_task = attach_fitbit(active.db)
        app.state.fitbit = fitbit_sync
        try:
            yield
        finally:
            if fitbit_task is not None:
                fitbit_task.cancel()
            await active.stop()

    app = FastAPI(lifespan=lifespan)
    if pipeline is not None:
        app.state.pipeline = pipeline
    app.add_middleware(CORSMiddleware,
                       allow_origins=["http://localhost:3000"],
                       allow_credentials=True, allow_methods=["*"],
                       allow_headers=["*"])
    app.include_router(router)
    app.include_router(fitbit_router)
    return app
