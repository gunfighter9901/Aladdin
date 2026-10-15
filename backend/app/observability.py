"""Opik full traces for the Google ADK agent. No Ragas wrapper."""

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
