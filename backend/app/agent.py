"""Google ADK ReAct agent. Not a hand-rolled loop and not LangChain.

Annotations are evaluated at runtime on purpose. ADK excludes ``tool_context``
from the model schema only when it can see the real ``ToolContext`` type.
"""

import inspect
import logging
from typing import Any

try:
    from google.adk.tools.tool_context import ToolContext
except ImportError:  # pragma: no cover - layout differs across ADK releases
    from google.adk.tools import ToolContext

from app.config import settings
from app.errors import AppError
from app.observability import chunk_preview, flush_tracer, instrument_agent, truncate, update_span
from app.prompts import TUTOR_INSTRUCTION
from app.runtime_context import active_source_ids, push_artifact
from app.safety import safe_error_message

logger = logging.getLogger("educator.agent")

APP_NAME = "educator"
USER_ID = "local"


def build_web_search_tool(model: str) -> Any:
    """Google Search grounding, isolated so it can sit beside function tools."""
    from google.adk.agents import LlmAgent
    from google.adk.tools.agent_tool import AgentTool

    try:
        from google.adk.tools import google_search
    except ImportError:
        from google.adk.tools.google_search_tool import google_search

    search_agent = LlmAgent(
        name="web_search",
        model=model,
        description=(
            "Search the public web with Google Search grounding when the selected "
            "study materials do not contain the answer, or when the student asks for the web."
        ),
        instruction=(
            "You are a web research specialist for a student. Answer the query with "
            "Google Search grounding. Summarize the findings in a few factual sentences "
            "and name the pages you relied on. Do not invent citations to the student's files. "
            "If search is unavailable, say so."
        ),
        tools=[google_search],
    )
    kwargs: dict[str, Any] = {"agent": search_agent}
    parameters = inspect.signature(AgentTool.__init__).parameters
    if "propagate_grounding_metadata" in parameters:
        kwargs["propagate_grounding_metadata"] = True
    return AgentTool(**kwargs)


async def rag_retriever(query: str, tool_context: ToolContext) -> dict[str, Any]:
    """Search the student's selected study materials.

    Uses a Qdrant $in filter (MatchAny) on sourceId. An empty selection returns
    nothing and does not search. Muted sources are not in the id list, so they
    cannot appear in hits. Cite hits as [Source: filename, Chunk: chunkId].

    Args:
        query: What the student is asking, phrased as a search query.

    Returns:
        Hits limited to the active sources, with filename and chunkId for citations.
    """
    # NOTE: No @track here on purpose. ADK's OpikTracer already opens a tool
    # Span for this call (via OpenTelemetry). update_span() below enriches
    # that Span with the query and RAG chunks, so the Opik web app shows
    # Trace -> tool Span -> qdrant Span without duplicate spans.
    import asyncio

    from app.vectorstore import get_store

    if not query.strip():
        update_span(output={"status": "error", "hit_count": 0})
        return {"status": "error", "message": "A search query is required.", "hits": []}
    ids = active_source_ids(tool_context)
    if not ids:
        update_span(
            input={"query": truncate(query, 1000), "active_source_ids": []},
            output={"status": "empty", "hit_count": 0},
        )
        return {
            "status": "empty",
            "message": "No sources are selected. Retrieval returned nothing.",
            "hits": [],
        }
    hits = await asyncio.to_thread(get_store().search, query, ids, settings.retrieval_limit)
    if not hits:
        update_span(
            input={"query": truncate(query, 1000), "active_source_ids": ids},
            output={"status": "no_hits", "hit_count": 0},
        )
        return {
            "status": "no_hits",
            "message": "The selected materials do not contain a matching passage.",
            "hits": [],
        }
    payload = {
        "status": "ok",
        "message": "Use only these passages. Cite each claim you take from them.",
        "hits": [
            {
                "sourceId": hit.source_id,
                "filename": hit.filename,
                "chunkId": hit.chunk_id,
                "pageNumber": hit.page_number,
                "score": round(hit.score, 4),
                "text": hit.text[:2500],
            }
            for hit in hits
        ],
    }
    update_span(
        input={"query": truncate(query, 1000), "active_source_ids": ids},
        output={"status": "ok", "hit_count": len(hits)},
        metadata={"chunks": chunk_preview(hits)},
    )
    return payload


async def podcast_generator(focus: str, tool_context: ToolContext) -> dict[str, Any]:
    """Write a Joe-and-Jane study podcast from the selected sources and synthesize speech.

    Joe is the host and uses the Puck voice. Jane is the expert and uses the Kore voice.
    Audio is 24 kHz. The full audio is shown in the studio panel; this result is the script.

    Args:
        focus: The concept or question the podcast should teach.
    """
    from app.generators import generate_podcast

    ids = active_source_ids(tool_context)
    if not ids:
        update_span(
            input={"focus": truncate(focus, 1000)},
            output={"status": "empty"},
        )
        return {"status": "empty", "message": "Select at least one source before making a podcast."}
    try:
        payload = await generate_podcast(ids, focus)
    except Exception as exc:
        update_span(
            input={"focus": truncate(focus, 1000), "source_ids": ids},
            output={"status": "error"},
            metadata={"error": truncate(_tool_error(exc), 500)},
        )
        return {"status": "error", "message": _tool_error(exc)}
    push_artifact("podcast", payload, tool_context)
    update_span(
        input={"focus": truncate(focus, 1000), "source_ids": ids},
        output={
            "status": "ok",
            "title": payload.get("title"),
            "turns": len(payload.get("turns") or []),
            "audioReady": bool(payload.get("audioBase64")),
        },
        metadata={"audio_error": payload.get("audioError")},
    )
    return {
        "status": "ok",
        "title": payload.get("title"),
        "script": payload.get("script"),
        "audioReady": bool(payload.get("audioBase64")),
        "audioError": payload.get("audioError"),
    }


async def quiz_generator(focus: str, count: int, tool_context: ToolContext) -> dict[str, Any]:
    """Build an active-recall quiz and flashcards from the selected sources.

    Args:
        focus: The concept the questions should test.
        count: How many questions to write, between 1 and 12.
    """
    from app.generators import generate_quiz

    ids = active_source_ids(tool_context)
    if not ids:
        update_span(output={"status": "empty"})
        return {"status": "empty", "message": "Select at least one source before making a quiz."}
    try:
        payload = await generate_quiz(ids, focus, max(1, min(int(count or 5), 12)))
    except Exception as exc:
        update_span(output={"status": "error"}, metadata={"error": truncate(_tool_error(exc), 500)})
        return {"status": "error", "message": _tool_error(exc)}
    push_artifact("quiz", payload, tool_context)
    questions = payload.get("questions") or []
    update_span(
        input={"focus": truncate(focus, 1000), "count": count, "source_ids": ids},
        output={"status": "ok", "title": payload.get("title"), "question_count": len(questions)},
    )
    return {"status": "ok", **payload}


async def infographic_creator(focus: str, diagram_type: str, tool_context: ToolContext) -> dict[str, Any]:
    """Create Mermaid.js markup for a mindmap, flowchart, timeline, or sequence diagram.

    Args:
        focus: The concept to visualize.
        diagram_type: One of mindmap, flowchart, timeline, sequence.
    """
    from app.generators import generate_infographic

    ids = active_source_ids(tool_context)
    if not ids:
        update_span(output={"status": "empty"})
        return {"status": "empty", "message": "Select at least one source before making a diagram."}
    kind = diagram_type if diagram_type in {"mindmap", "flowchart", "timeline", "sequence"} else "mindmap"
    try:
        payload = await generate_infographic(ids, focus, kind)
    except Exception as exc:
        update_span(output={"status": "error"}, metadata={"error": truncate(_tool_error(exc), 500)})
        return {"status": "error", "message": _tool_error(exc)}
    push_artifact("infographic", payload, tool_context)
    update_span(
        input={"focus": truncate(focus, 1000), "diagram_type": kind, "source_ids": ids},
        output={"status": "ok", "title": payload.get("title")},
        metadata={"mermaid": truncate((payload.get("mermaid") or ""), 2000)},
    )
    return {"status": "ok", **payload}


def _tool_error(exc: Exception) -> str:
    if isinstance(exc, AppError):
        return exc.message
    return safe_error_message(exc)


class AgentRuntime:
    def __init__(self) -> None:
        self.agent = None
        self.runner = None
        self.session_service = None
        self.tracer = None
        self.ready = False

    def start(self) -> None:
        from google.adk.agents import LlmAgent
        from google.adk.runners import Runner
        from google.adk.sessions import DatabaseSessionService

        self.session_service = DatabaseSessionService(
            db_url=f"sqlite+aiosqlite:///{settings.adk_sqlite_path}"
        )
        web_search = build_web_search_tool(settings.reasoning_model)
        self.agent = LlmAgent(
            name="educator",
            model=settings.reasoning_model,
            description="A local tutor that answers from the student's selected materials and cites them.",
            instruction=TUTOR_INSTRUCTION,
            tools=[
                rag_retriever,
                web_search,
                podcast_generator,
                quiz_generator,
                infographic_creator,
            ],
        )
        self.tracer = instrument_agent(self.agent)
        self.runner = Runner(
            agent=self.agent,
            app_name=APP_NAME,
            session_service=self.session_service,
        )
        self.ready = True
        logger.info("ADK educator agent is ready")

    async def prepare(self) -> None:
        service = self.session_service
        if service is None:
            return
        prepare = getattr(service, "prepare_tables", None) or getattr(service, "create_tables", None)
        if prepare is None:
            return
        result = prepare()
        if inspect.isawaitable(result):
            await result

    def shutdown(self) -> None:
        flush_tracer(self.tracer)
        self.ready = False


runtime = AgentRuntime()
