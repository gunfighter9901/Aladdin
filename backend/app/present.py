"""Map database rows to API models. JSON columns are parsed here, not in the client."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.models import Artifact, ChatSession, Message, Source
from app.schemas import ArtifactOut, CitationOut, MessageOut, SessionOut, SourceOut, TraceStep

logger = logging.getLogger("educator.present")


def source_out(source: Source) -> SourceOut:
    return SourceOut(
        id=source.id,
        filename=source.filename,
        title=source.title,
        kind=source.kind,
        origin=source.origin,
        summary=source.summary,
        chunkCount=source.chunk_count,
        active=source.active,
        status=source.status,
        error=source.error,
        createdAt=source.created_at,
    )


def session_out(chat: ChatSession) -> SessionOut:
    return SessionOut(
        id=chat.id,
        title=chat.title,
        createdAt=chat.created_at,
        updatedAt=chat.updated_at,
    )


def message_out(message: Message) -> MessageOut:
    artifacts = _load(message.artifacts_json, {})
    return MessageOut(
        id=message.id,
        role=message.role,
        content=message.content,
        citations=_models(_load(message.citations_json, []), CitationOut),
        trace=_models(_load(message.trace_json, []), TraceStep),
        artifacts=artifacts if isinstance(artifacts, dict) else {},
        createdAt=message.created_at,
    )


def artifact_out(artifact: Artifact) -> ArtifactOut:
    payload = _load(artifact.payload_json, {})
    return ArtifactOut(
        id=artifact.id,
        kind=artifact.kind,
        title=artifact.title,
        payload=payload if isinstance(payload, dict) else {"value": payload},
        createdAt=artifact.created_at,
    )


def _models(items: Any, model: type[CitationOut] | type[TraceStep]) -> list[Any]:
    parsed = []
    for item in items if isinstance(items, list) else []:
        try:
            parsed.append(model.model_validate(item))
        except Exception:
            continue
    return parsed


def _load(raw: str, fallback: Any) -> Any:
    try:
        return json.loads(raw or "")
    except json.JSONDecodeError:
        logger.warning("Dropping a malformed JSON column; returning the fallback")
        return fallback
