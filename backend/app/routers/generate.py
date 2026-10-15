"""Studio endpoints for podcasts, quizzes, and Mermaid diagrams."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.database import get_session
from app.generators import generate_infographic, generate_podcast, generate_quiz
from app.models import Artifact, ChatSession, utcnow
from app.observability import truncate, update_trace
from app.schemas import GenerateRequest

from opik import opik_context, track

router = APIRouter(prefix="/api", tags=["studio"])


@router.post("/generate-podcast")
@track(name="studio_podcast", tags=["educator", "studio"], capture_output=False, ignore_arguments=["db"])
async def podcast(body: GenerateRequest, db: Session = Depends(get_session)) -> dict:
    _bind_studio_thread(body.sessionId, "podcast", body.focus)
    payload = await generate_podcast(body.sourceIds, body.focus)
    _finish_studio_trace("podcast", payload)
    _save(db, "podcast", payload, body.sessionId)
    return payload


@router.post("/generate-quiz")
@track(name="studio_quiz", tags=["educator", "studio"], ignore_arguments=["db"])
async def quiz(body: GenerateRequest, db: Session = Depends(get_session)) -> dict:
    _bind_studio_thread(body.sessionId, "quiz", body.focus)
    payload = await generate_quiz(body.sourceIds, body.focus, body.count)
    update_trace(
        output={"title": payload.get("title"), "question_count": len(payload.get("questions") or [])},
    )
    _save(db, "quiz", payload, body.sessionId)
    return payload


@router.post("/generate-infographic")
@track(name="studio_infographic", tags=["educator", "studio"], ignore_arguments=["db"])
async def infographic(body: GenerateRequest, db: Session = Depends(get_session)) -> dict:
    _bind_studio_thread(body.sessionId, "infographic", body.focus)
    payload = await generate_infographic(body.sourceIds, body.focus, body.diagramType)
    update_trace(
        output={"title": payload.get("title"), "diagram_type": payload.get("diagramType")},
        metadata={"mermaid": truncate(payload.get("mermaid") or "", 2000)},
    )
    _save(db, "infographic", payload, body.sessionId)
    return payload


def _bind_studio_thread(session_id: str | None, kind: str, focus: str) -> None:
    # Studio generations belong to the same Thread as the chat session
    # when a session id is present, so Threads show chat + studio together.
    if not session_id:
        return
    try:
        opik_context.update_current_trace(
            thread_id=session_id,
            tags=["educator", "studio"],
            metadata={"studio_kind": kind},
            input={"session_id": session_id, "focus": truncate(focus or "", 1000)},
        )
    except Exception:
        pass


def _finish_studio_trace(kind: str, payload: dict) -> None:
    # Audio base64 is large; never log it to the Trace.
    try:
        output = {key: value for key, value in payload.items() if key != "audioBase64"}
        if "script" in output:
            output["script"] = truncate(output.get("script") or "", 2000)
        update_trace(output=output)
    except Exception:
        pass


def _save(db: Session, kind: str, payload: dict, session_id: str | None) -> None:
    if session_id and db.get(ChatSession, session_id) is None:
        session_id = None
    db.add(
        Artifact(
            id=str(uuid.uuid4()),
            session_id=session_id,
            kind=kind,
            title=str(payload.get("title") or kind)[:180],
            payload_json=json.dumps(payload),
            created_at=utcnow(),
        )
    )
    db.commit()
