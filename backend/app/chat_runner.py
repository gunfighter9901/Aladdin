"""Run one ADK turn and consume the full event stream so Opik records the final response."""

from __future__ import annotations

import inspect
import json
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

from google.genai import types as genai_types
from sqlmodel import Session

from app.agent import APP_NAME, USER_ID, runtime
from app.config import settings
from app.database import engine
from app.models import ChatSession, Message, Source, utcnow
from app.observability import bind_thread, chunk_preview, flush_tracer, truncate, update_trace
from app.runtime_context import (
    begin_artifacts,
    current_artifacts,
    reset_active_source_ids,
    reset_artifacts,
    set_active_source_ids,
)
from app.safety import safe_error_message
from app.vectorstore import Hit

CITATION_RE = re.compile(
    r"\[Source:\s*(?P<filename>[^,\[\]]+?),\s*Chunk:\s*(?P<chunk>[^\[\]]+?)\s*\]"
)


from opik import opik_context, track


@track(name="educator_turn", tags=["educator", "chat"], capture_output=False)
async def run_turn(
    session_id: str,
    message: str,
    active_source_ids: list[str],
) -> AsyncIterator[dict[str, Any]]:
    # One user request = one Trace. Group all turns of a study session
    # into one Thread so the Opik web app shows Threads > Traces > Spans.
    if session_id:
        try:
            opik_context.update_current_trace(
                thread_id=session_id,
                tags=["educator", "chat"],
                metadata={"user_id": USER_ID, "app": APP_NAME},
                input={
                    "session_id": session_id,
                    "message": truncate(message, 4000),
                    "active_source_ids": list(active_source_ids or []),
                },
            )
        except Exception:
            pass
        bind_thread(session_id)
    if not runtime.ready or runtime.runner is None:
        yield {
            "event": "error",
            "data": {
                "message": "The tutor agent is not ready. Restart the API and check the logs.",
                "status": 503,
            },
        }
        return

    text = message.strip()
    if not text:
        yield {"event": "error", "data": {"message": "Write a question first.", "status": 400}}
        return

    with Session(engine) as db:
        chat = db.get(ChatSession, session_id)
        if chat is None:
            yield {"event": "error", "data": {"message": "That study session was not found.", "status": 404}}
            return
        sources = _selected_sources(db, active_source_ids)
        # Snapshot ORM rows before the session closes. Accessing attributes
        # after commit/close would raise DetachedInstanceError because
        # commit expires state and close detaches instances.
        source_infos = [{"id": row.id, "filename": row.filename} for row in sources]
        user_message = Message(
            id=str(uuid.uuid4()),
            session_id=session_id,
            role="user",
            content=text,
            created_at=utcnow(),
        )
        db.add(user_message)
        if chat.title == "New study session":
            chat.title = _clean_title(text)
        chat.updated_at = utcnow()
        db.add(chat)
        db.commit()

    yield {"event": "status", "data": {"text": "The tutor is reading the selected sources."}}
    await _ensure_adk_session(session_id)

    ids = [info["id"] for info in source_infos][: max(1, settings.max_sources_per_query)]
    id_token = set_active_source_ids(ids)
    artifact_token = begin_artifacts()
    trace: list[dict[str, Any]] = []
    retrieved: list[Hit] = []
    final_text = ""
    partial_text: list[str] = []
    failed = False
    failure_message = ""
    failure_status = 502
    content = _user_content(_agent_message(text, source_infos))

    try:
        run_kwargs: dict[str, Any] = {
            "user_id": USER_ID,
            "session_id": session_id,
            "new_message": content,
            "state_delta": {"active_source_ids": ids, "session_id": session_id},
        }
        try:
            stream = runtime.runner.run_async(**run_kwargs)
        except TypeError:
            run_kwargs.pop("state_delta", None)
            stream = runtime.runner.run_async(**run_kwargs)
        # Do not break when is_final_response() is true. Opik records the final
        # response only if the whole ADK event stream is consumed.
        async for event in stream:
            for step in _trace_steps(event):
                trace.append(step)
                if step["type"] == "tool_result" and step.get("name") == "rag_retriever":
                    retrieved.extend(_hits_from_result(step.get("result")))
                yield {"event": "trace", "data": _shrink_step(step)}
            event_text = _event_text(event)
            partial = bool(getattr(event, "partial", False))
            if event_text and partial:
                partial_text.append(event_text)
                yield {"event": "token", "data": {"text": event_text}}
            if event_text and not partial and _is_final(event):
                final_text = event_text
    except Exception as exc:
        # Do not persist the failure as an assistant answer: it would poison
        # the stored history and the next turn's context. Record it in the
        # trace, then yield an error below when the model produced nothing.
        failed = True
        failure_message = safe_error_message(exc)
        failure_status = getattr(exc, "status_code", 502) or 502
        try:
            failure_status = int(failure_status)
        except (TypeError, ValueError):
            failure_status = 502
        trace.append({"type": "status", "name": "error", "text": failure_message})
    finally:
        flush_tracer(runtime.tracer)
        reset_active_source_ids(id_token)
        # Artifacts are read just below, before the token is reset.
        # Merge both channels: ContextVar fast path + ADK state fallback for
        # tools that executed outside the ContextVar context.
        state_items = await _read_state_artifacts(session_id)
        artifacts = current_artifacts(state_items)
        # Last-resort fallback: if a studio tool reported status ok but its
        # pushed artifact never arrived (context/state channel lost), rebuild
        # a playable-or-readable artifact from the FULL in-memory tool result.
        # Context/state wins when present (it alone carries podcast audio).
        for kind, fallback in _fallback_artifacts(trace).items():
            artifacts.setdefault(kind, fallback)
        if any(step.get("type") == "tool_result" and step.get("name") in _STUDIO_TOOLS for step in trace):
            import logging as _logging

            _logging.getLogger("educator.chat").info(
                "Turn artifacts collected=%s tools=%s",
                sorted(artifacts.keys()),
                sorted({step.get("name") for step in trace if step.get("type") == "tool_result" and step.get("name")}),
            )
        reset_artifacts(artifact_token)

    if not final_text:
        final_text = "".join(partial_text).strip()
    if not final_text:
        if failed:
            update_trace(
                output={"error": failure_message, "hit_count": len(retrieved)},
                metadata={"active_source_ids": ids, "trace_steps": len(trace)},
            )
            yield {"event": "error", "data": {"message": failure_message, "status": failure_status}}
            return
        final_text = "The tutor did not produce an answer. Try asking again in a moment."

    citations = _citations(final_text, retrieved)
    message_id = str(uuid.uuid4())
    with Session(engine) as db:
        chat = db.get(ChatSession, session_id)
        db.add(
            Message(
                id=message_id,
                session_id=session_id,
                role="assistant",
                content=final_text,
                citations_json=json.dumps(citations),
                trace_json=json.dumps([_shrink_step(step) for step in trace]),
                artifacts_json=json.dumps(artifacts),
                created_at=utcnow(),
            )
        )
        if chat is not None:
            chat.updated_at = utcnow()
            db.add(chat)
        db.commit()

    # Close the Trace with agent I/O, tool calls, and RAG chunks so the
    # Opik web app shows Threads > Traces > Spans with full context.
    tool_names = sorted({step.get("name") for step in trace if step.get("name")})
    update_trace(
        output={
            "message_id": message_id,
            "content": truncate(final_text, 4000),
            "citations": citations,
            "artifact_kinds": sorted(artifacts.keys()) if isinstance(artifacts, dict) else [],
        },
        metadata={
            "active_source_ids": ids,
            "tool_calls": tool_names,
            "hit_count": len(retrieved),
            "chunks": chunk_preview(retrieved),
            "trace_steps": len(trace),
        },
    )

    yield {
        "event": "done",
        "data": {
            "sessionId": session_id,
            "messageId": message_id,
            "content": final_text,
            "citations": citations,
            "trace": [_shrink_step(step) for step in trace],
            "artifacts": artifacts,
        },
    }


def _clean_title(text: str) -> str:
    """Single-line session title without control characters."""
    cleaned = " ".join(text.split())
    return cleaned[:72] or "Study session"


def _selected_sources(db: Session, source_ids: list[str]) -> list[Source]:
    from app.config import settings as _settings

    selected: list[Source] = []
    seen: set[str] = set()
    for source_id in (source_ids or [])[: max(1, _settings.max_sources_per_query)]:
        if not source_id or source_id in seen:
            continue
        source = db.get(Source, source_id)
        if source is None or source.status != "ready":
            continue
        seen.add(source_id)
        selected.append(source)
    return selected


def _agent_message(message: str, sources: list[dict[str, str]]) -> str:
    if not sources:
        catalog = "Selected sources: none. Retrieval must return nothing. Do not invent citations."
    else:
        lines = "\n".join(f"- {source['filename']} (sourceId: {source['id']})" for source in sources)
        catalog = (
            "Selected sources (only these may be retrieved or cited):\n"
            f"{lines}"
        )
    return f"{catalog}\n\nStudent:\n{message}"


def _user_content(text: str) -> Any:
    if hasattr(genai_types.Part, "from_text"):
        part = genai_types.Part.from_text(text=text)
    else:
        part = genai_types.Part(text=text)
    return genai_types.Content(role="user", parts=[part])


async def _ensure_adk_session(session_id: str) -> None:
    import logging

    logger = logging.getLogger("educator.agent")
    service = runtime.session_service
    existing = service.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=session_id)
    if inspect.isawaitable(existing):
        existing = await existing
    if existing is not None:
        return
    try:
        created = service.create_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=session_id,
        )
    except TypeError:
        # Older ADK releases lack the session_id kwarg. Creating without an id
        # would orphan conversational memory (Runner would still look up our
        # session_id and miss). Fail loudly so the version skew is visible
        # instead of silently losing history every turn.
        logger.error("ADK session service does not accept session_id; refusing orphan session")
        raise
    if inspect.isawaitable(created):
        await created


async def _read_state_artifacts(session_id: str) -> list[dict[str, Any]] | None:
    """Best-effort read of artifacts stored in ADK session state."""
    try:
        service = runtime.session_service
        if service is None:
            return None
        existing = service.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=session_id)
        if inspect.isawaitable(existing):
            existing = await existing
        if existing is None:
            return None
        state = getattr(existing, "state", None)
        if state is None:
            return None
        try:
            value = state.get("educator_artifacts") if hasattr(state, "get") else getattr(state, "educator_artifacts", None)
        except Exception:
            value = getattr(state, "educator_artifacts", None)
        return value if isinstance(value, list) else None
    except Exception:
        return None


def _is_final(event: Any) -> bool:
    checker = getattr(event, "is_final_response", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            return False
    return False


def _event_text(event: Any) -> str:
    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None) or []
    chunks: list[str] = []
    for part in parts:
        if getattr(part, "thought", False):
            continue
        if getattr(part, "function_call", None) or getattr(part, "function_response", None):
            continue
        text = getattr(part, "text", None)
        if text:
            chunks.append(text)
    return "".join(chunks)


def _trace_steps(event: Any) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for call in _function_calls(event):
        steps.append(
            {
                "type": "tool_call",
                "name": call.get("name"),
                "args": call.get("args"),
            }
        )
    for response in _function_responses(event):
        steps.append(
            {
                "type": "tool_result",
                "name": response.get("name"),
                "result": response.get("response"),
            }
        )
    return steps


def _function_calls(event: Any) -> list[dict[str, Any]]:
    getter = getattr(event, "get_function_calls", None)
    calls = list(getter() or []) if callable(getter) else []
    if not calls:
        content = getattr(event, "content", None)
        for part in getattr(content, "parts", None) or []:
            call = getattr(part, "function_call", None)
            if call is not None:
                calls.append(call)
    parsed = []
    for call in calls:
        parsed.append(
            {
                "name": getattr(call, "name", None),
                "args": _plain(getattr(call, "args", None)),
            }
        )
    return parsed


def _function_responses(event: Any) -> list[dict[str, Any]]:
    getter = getattr(event, "get_function_responses", None)
    responses = list(getter() or []) if callable(getter) else []
    if not responses:
        content = getattr(event, "content", None)
        for part in getattr(content, "parts", None) or []:
            response = getattr(part, "function_response", None)
            if response is not None:
                responses.append(response)
    parsed = []
    for response in responses:
        parsed.append(
            {
                "name": getattr(response, "name", None),
                "response": _plain(getattr(response, "response", None)),
            }
        )
    return parsed


_STUDIO_TOOLS = ("podcast_generator", "quiz_generator", "infographic_creator")


def _fallback_artifacts(trace: list[dict[str, Any]]) -> dict[str, Any]:
    """Rebuild studio artifacts from full tool results when push delivery failed.

    Uses the UNSHRUNK in-memory trace (done.trace is shrunk and may truncate
    large payloads). Podcast results carry script+turns but no audioBase64, so
    the fallback shows the script with an explanatory hint; quiz/infographic
    results already carry their full payloads.
    """
    found: dict[str, Any] = {}
    for step in trace:
        if step.get("type") != "tool_result":
            continue
        name = step.get("name")
        result = step.get("result")
        if not isinstance(result, dict) or result.get("status") != "ok":
            continue
        if name == "podcast_generator" and "podcast" not in found:
            turns = [t for t in (result.get("turns") or []) if isinstance(t, dict)]
            found["podcast"] = {
                "title": str(result.get("title") or "Study podcast"),
                "script": str(result.get("script") or ""),
                "turns": turns,
                "audioBase64": str(result.get("audioBase64") or ""),
                "mimeType": "audio/wav",
                "sampleRateHz": 24000,
                "voices": {"Joe": "Puck", "Jane": "Kore"},
                "audioError": result.get("audioError")
                or "Audio did not arrive with this turn. Regenerate from the Studio panel for sound.",
                "truncated": bool(result.get("truncated")),
            }
        elif name == "quiz_generator" and "quiz" not in found:
            questions = result.get("questions")
            if isinstance(questions, list) and questions:
                found["quiz"] = {
                    "title": str(result.get("title") or "Active recall"),
                    "questions": questions,
                    "flashcards": result.get("flashcards") or [],
                    "requestedCount": result.get("requestedCount"),
                    "returnedCount": result.get("returnedCount"),
                }
        elif name == "infographic_creator" and "infographic" not in found:
            mermaid = result.get("mermaid")
            if isinstance(mermaid, str) and mermaid.strip():
                found["infographic"] = {
                    "title": str(result.get("title") or "Diagram"),
                    "diagramType": result.get("diagramType") or "mindmap",
                    "mermaid": mermaid,
                }
    return found


def _hits_from_result(result: Any) -> list[Hit]:
    if not isinstance(result, dict):
        return []
    hits: list[Hit] = []
    for item in result.get("hits") or []:
        if not isinstance(item, dict):
            continue
        source_id = item.get("sourceId")
        chunk_id = item.get("chunkId")
        filename = item.get("filename")
        if not source_id or not chunk_id or not filename:
            continue
        page = item.get("pageNumber")
        hits.append(
            Hit(
                source_id=str(source_id),
                filename=str(filename),
                chunk_id=str(chunk_id),
                chunk_index=0,
                page_number=int(page) if isinstance(page, int) else None,
                text=str(item.get("text") or ""),
                score=float(item.get("score") or 0.0),
            )
        )
    return hits


def _citations(text: str, hits: list[Hit]) -> list[dict[str, Any]]:
    lookup: dict[tuple[str, str], Hit] = {}
    for hit in hits:
        lookup[(hit.filename, hit.chunk_id)] = hit
        lookup[(hit.filename.lower(), hit.chunk_id.lower())] = hit
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for match in CITATION_RE.finditer(text or ""):
        filename = match.group("filename").strip()
        chunk_id = match.group("chunk").strip()
        hit = lookup.get((filename, chunk_id)) or lookup.get((filename.lower(), chunk_id.lower()))
        if hit is None:
            continue
        key = (hit.source_id, hit.chunk_id)
        if key in seen:
            continue
        seen.add(key)
        found.append(hit.citation())
    return found


def _shrink_step(step: dict[str, Any]) -> dict[str, Any]:
    shrunk = dict(step)
    if "result" in shrunk:
        shrunk["result"] = _shrink(shrunk["result"])
    if "args" in shrunk:
        shrunk["args"] = _shrink(shrunk["args"])
    return shrunk


def _shrink(value: Any, limit: int = 1800) -> Any:
    if isinstance(value, dict) and "hits" in value and isinstance(value.get("hits"), list):
        preview = dict(value)
        preview["hits"] = [
            {
                "filename": hit.get("filename"),
                "chunkId": hit.get("chunkId"),
                "sourceId": hit.get("sourceId"),
                "pageNumber": hit.get("pageNumber"),
                "score": hit.get("score"),
            }
            for hit in value["hits"][:8]
            if isinstance(hit, dict)
        ]
        return preview
    try:
        encoded = json.dumps(value, default=str)
    except TypeError:
        encoded = str(value)
    if len(encoded) <= limit:
        return value
    return {"truncated": True, "preview": encoded[:limit]}


def _plain(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if hasattr(value, "items"):
        try:
            return {str(key): _plain(item) for key, item in value.items()}
        except Exception:
            return str(value)
    return str(value)
