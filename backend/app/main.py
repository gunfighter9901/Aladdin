"""Local educator API. No auth. The browser never receives provider keys."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from starlette import status

from app.agent import runtime
from app.config import settings
from app.database import init_db
from app.errors import AppError
from app.middleware import RequestContextMiddleware
from app.routers import chat, generate, sessions, sources
from app.safety import redact, safe_error_message
from app.schemas import HealthOut
from app.vectorstore import close_store, get_store

logger = logging.getLogger("educator")


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            try:
                record.args = redact(str(record.args))
            except Exception:
                pass
        return True


def _configure_logging() -> None:
    level = settings.log_level_name()
    logging.basicConfig(level=getattr(logging, level, logging.INFO), format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger().addFilter(_RedactFilter())


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    init_db()
    try:
        from app.chunking import warm_chunker

        await asyncio.to_thread(warm_chunker)
    except Exception as exc:
        logger.warning("Embedding tokenizer is not ready yet: %s", safe_error_message(exc))
    try:
        await asyncio.to_thread(get_store().ensure_collection)
    except Exception as exc:
        logger.warning("Vector collection is not ready yet: %s", safe_error_message(exc))
    try:
        runtime.start()
        await runtime.prepare()
    except Exception as exc:
        logger.error("Agent failed to start: %s", safe_error_message(exc))
    yield
    runtime.shutdown()
    close_store()


app = FastAPI(
    title="Educator",
    description="Local agentic RAG tutor. Sources, citations, podcasts, quizzes, and diagrams.",
    version="1.1.0",
    lifespan=lifespan,
)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(sources.router)
app.include_router(sessions.router)
app.include_router(chat.router)
app.include_router(generate.router)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message, "code": exc.code},
        headers={"X-Request-ID": getattr(request.state, "request_id", "")},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": "That request was not valid.", "code": "validation_error"},
        headers={"X-Request-ID": getattr(request.state, "request_id", "")},
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error request_id=%s", getattr(request.state, "request_id", ""))
    return JSONResponse(
        status_code=500,
        content={"detail": "Something went wrong on the server.", "code": "internal_error"},
        headers={"X-Request-ID": getattr(request.state, "request_id", "")},
    )


@app.get("/api/health", response_model=HealthOut)
async def health() -> HealthOut:
    qdrant = "ready"
    try:
        await asyncio.to_thread(get_store().count)
    except Exception:
        qdrant = "unavailable"
    return HealthOut(
        status="ok" if runtime.ready else "starting",
        geminiConfigured=settings.gemini_configured(),
        opikConfigured=settings.opik_configured(),
        collection=settings.collection_name,
        qdrant=qdrant,
    )


@app.get("/api/ready")
async def ready() -> dict:
    """Readiness for restarts: agent, vector store, and database must all answer."""
    checks: dict[str, str] = {"agent": "ready" if runtime.ready else "starting"}
    try:
        await asyncio.to_thread(get_store().count)
        checks["qdrant"] = "ready"
    except Exception:
        checks["qdrant"] = "unavailable"
    try:
        from sqlmodel import Session as _Session
        from sqlmodel import select as _select

        from app.database import engine as _engine
        from app.models import Source as _Source

        with _Session(_engine) as _db:
            _db.exec(_select(_Source)).first()
        checks["database"] = "ready"
    except Exception:
        checks["database"] = "unavailable"
    ok = all(value == "ready" for value in checks.values())
    return {"status": "ok" if ok else "starting", "checks": checks}
