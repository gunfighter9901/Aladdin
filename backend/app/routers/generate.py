"""Studio endpoints for podcasts, quizzes, and Mermaid diagrams."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.database import get_session
from app.generators import generate_infographic, generate_podcast, generate_quiz
from app.models import Artifact, ChatSession, utcnow
from app.schemas import GenerateRequest

router = APIRouter(prefix="/api", tags=["studio"])


@router.post("/generate-podcast")
async def podcast(body: GenerateRequest, db: Session = Depends(get_session)) -> dict:
    payload = await generate_podcast(body.sourceIds, body.focus)
    _save(db, "podcast", payload, body.sessionId)
    return payload


@router.post("/generate-quiz")
async def quiz(body: GenerateRequest, db: Session = Depends(get_session)) -> dict:
    payload = await generate_quiz(body.sourceIds, body.focus, body.count)
    _save(db, "quiz", payload, body.sessionId)
    return payload


@router.post("/generate-infographic")
async def infographic(body: GenerateRequest, db: Session = Depends(get_session)) -> dict:
    payload = await generate_infographic(body.sourceIds, body.focus, body.diagramType)
    _save(db, "infographic", payload, body.sessionId)
    return payload


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
