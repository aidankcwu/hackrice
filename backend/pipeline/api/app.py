"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config import Settings, get_settings
from .routes import router
from .wiring import Pipeline, build_pipeline


def create_app(pipeline: Pipeline | None = None, *, settings: Settings | None = None,
               **build_kwargs) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        active = pipeline
        if active is None:
            active = build_pipeline(settings or get_settings(), **build_kwargs)
        app.state.pipeline = active
        await active.start()
        try:
            yield
        finally:
            await active.stop()

    app = FastAPI(lifespan=lifespan)
    if pipeline is not None:
        app.state.pipeline = pipeline
    app.add_middleware(CORSMiddleware,
                       allow_origins=["http://localhost:3000"],
                       allow_credentials=True, allow_methods=["*"],
                       allow_headers=["*"])
    app.include_router(router)
    return app
