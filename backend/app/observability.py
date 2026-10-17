"""Opik traces, spans, and threads. No Ragas wrapper.

Convention (kept simple):
- One user request start-to-finish = one Trace. The outermost @track
  function becomes the trace.
- Every internal action = one Span. Each @track function called inside
  the trace becomes a nested span.
- All traces from the same chat session = one Thread via
  thread_id=session_id on the trace.

Threads/Traces/Spans are outsourced to the Opik web app; this file only
adds the decorators and the thread binding. When Opik keys are missing,
OPIK_TRACK_DISABLE=true makes every @track a no-op.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import settings

logger = logging.getLogger("educator.opik")


def instrument_agent(agent: Any) -> Any:
    """Attach OpikTracer and walk the agent tree, including the search sub-agent."""
    from opik.integrations.adk import OpikTracer, track_adk_agent_recursive

    tracer = OpikTracer(
        name="educator-react-agent",
        tags=["educator", "rag", "react", "adk"],
        metadata={
            "framework": "google-adk",
            "model": settings.reasoning_model,
            "collection": settings.collection_name,
        },
        project_name=settings.opik_project_name or "educator-rag",
    )
    track_adk_agent_recursive(agent, tracer)
    if settings.opik_configured():
        logger.info("Opik tracing is attached for project %s", settings.opik_project_name)
    else:
        logger.info("Opik tracer is attached. Set OPIK_API_KEY or OPIK_URL_OVERRIDE to ship traces.")
    return tracer


def flush_tracer(tracer: Any) -> None:
    if tracer is None:
        return
    flush = getattr(tracer, "flush", None)
    if not callable(flush):
        return
    try:
        flush()
    except Exception:
        logger.warning("Opik flush failed; the study session was not interrupted.")
    try:
        import opik

        opik.flush_tracker()
    except Exception:
        pass


def bind_thread(thread_id: str, *, tags: list[str] | None = None, metadata: dict[str, Any] | None = None) -> None:
    """Group the current trace into a thread. Never raises."""
    if not thread_id:
        return
    try:
        from opik import opik_context

        opik_context.update_current_trace(thread_id=thread_id, tags=tags, metadata=metadata)
    except Exception:
        pass


def update_trace(
    *,
    input: dict[str, Any] | None = None,
    output: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> None:
    """Best-effort trace update. Never raises."""
    try:
        from opik import opik_context

        opik_context.update_current_trace(input=input, output=output, metadata=metadata, tags=tags)
    except Exception:
        pass


def update_span(
    *,
    input: dict[str, Any] | None = None,
    output: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    model: str | None = None,
) -> None:
    """Best-effort span update. Never raises."""
    try:
        from opik import opik_context

        kwargs: dict[str, Any] = {}
        if input is not None:
            kwargs["input"] = input
        if output is not None:
            kwargs["output"] = output
        if metadata is not None:
            kwargs["metadata"] = metadata
        if model is not None:
            kwargs["model"] = model
        if kwargs:
            opik_context.update_current_span(**kwargs)
    except Exception:
        pass


def truncate(text: Any, limit: int = 2000) -> str:
    raw = "" if text is None else str(text)
    if len(raw) <= limit:
        return raw
    return raw[:limit] + f"...[truncated {len(raw) - limit} chars]"


def chunk_preview(hits: Any, *, text_chars: int = 500, limit: int = 8) -> list[dict[str, Any]]:
    """Small JSON-safe preview of RAG chunks for Spans/Traces. Never raises."""
    try:
        items = list(hits)[:limit] if hits else []
    except Exception:
        return []
    preview: list[dict[str, Any]] = []
    for item in items:
        try:
            if isinstance(item, dict):
                preview.append(
                    {
                        "sourceId": item.get("sourceId"),
                        "filename": item.get("filename"),
                        "chunkId": item.get("chunkId"),
                        "pageNumber": item.get("pageNumber"),
                        "score": item.get("score"),
                        "text": truncate(item.get("text") or "", text_chars),
                    }
                )
            else:
                preview.append(
                    {
                        "sourceId": getattr(item, "source_id", None),
                        "filename": getattr(item, "filename", None),
                        "chunkId": getattr(item, "chunk_id", None),
                        "pageNumber": getattr(item, "page_number", None),
                        "score": round(float(getattr(item, "score", 0.0) or 0.0), 4),
                        "text": truncate(getattr(item, "text", "") or "", text_chars),
                    }
                )
        except Exception:
            continue
    return preview
