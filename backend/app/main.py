from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.api.routes import creatomate, downloads, health, jobs, public_media, templates, uploads
from app.config import get_settings
from app.core.logging import setup_logging, get_logger
from app.services.storage import StorageService
from app.workers.runner import worker

setup_logging()
logger = get_logger(__name__)
settings = get_settings()

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{settings.rate_limit_per_minute}/minute"],
    enabled=True,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    StorageService().ensure_dirs()
    await worker.start()
    logger.info("Application started (inline_worker=%s)", settings.use_inline_worker)
    yield
    await worker.stop()
    logger.info("Application stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Bulk Video Editor",
        version="2.0.0",
        lifespan=lifespan,
    )
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def correlation_id_middleware(request: Request, call_next):
        cid = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        request.state.correlation_id = cid
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = cid
        return response

    app.include_router(health.router)
    app.include_router(uploads.router)
    app.include_router(jobs.router)
    app.include_router(templates.router)
    app.include_router(downloads.router)
    app.include_router(creatomate.router)
    app.include_router(public_media.router)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):  # noqa: ARG001
        cid = getattr(request.state, "correlation_id", None) or str(uuid.uuid4())
        logger.exception("Unhandled error [%s]: %s", cid, exc)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "correlation_id": cid},
            headers={"X-Correlation-ID": cid},
        )

    return app


app = create_app()
