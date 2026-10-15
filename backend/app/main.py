"""Local educator API. No auth. The browser never receives provider keys."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.agent import runtime
from app.config import settings
from app.database import init_db
from app.errors import AppError
from app.routers import chat, generate, sessions, sources
from app.safety import redact, safe_error_message
from app.vectorstore import close_store, get_store

logger = logging.getLogger("educator")


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger().addFilter(_RedactFilter())


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    init_db()
    try:
        await asyncio.to_thread(get_store().ensure_collection)
    except Exception as exc:
        logger.warning("Vector collection is not ready yet: %s", safe_error_message(exc))
    runtime.start()
    await runtime.prepare()
    yield
    runtime.shutdown()
    close_store()


app = FastAPI(
    title="Educator",
    description="Local agentic RAG tutor. Sources, citations, podcasts, quizzes, and diagrams.",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(sources.router)
app.include_router(sessions.router)
app.include_router(chat.router)
app.include_router(generate.router)


@app.exception_handler(AppError)
async def app_error_handler(_request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.get("/api/health")
async def health() -> dict:
    import asyncio

    qdrant = "ready"
    try:
        await asyncio.to_thread(get_store().count)
    except Exception:
        qdrant = "unavailable"
    return {
        "status": "ok" if runtime.ready else "starting",
        "geminiConfigured": settings.gemini_configured(),
        "opikConfigured": settings.opik_configured(),
        "collection": settings.collection_name,
        "qdrant": qdrant,
    }
