"""Study sessions and their messages. Agent history is stored by ADK in SQLite."""

from __future__ import annotations

import inspect
import uuid

from fastapi import APIRouter, Depends, Response
from sqlmodel import Session, select

from app.agent import APP_NAME, USER_ID, runtime
from app.database import get_session
from app.errors import AppError
from app.models import Artifact, ChatSession, Message, utcnow
from app.present import artifact_out, message_out, session_out
from app.schemas import SessionCreate, SessionDetail, SessionOut

router = APIRouter(prefix="/api", tags=["sessions"])


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(db: Session = Depends(get_session)) -> list[SessionOut]:
    rows = db.exec(select(ChatSession).order_by(ChatSession.updated_at.desc())).all()
    return [session_out(row) for row in rows]


@router.post("/sessions", response_model=SessionOut, status_code=201)
async def create_or_switch_session(
    body: SessionCreate | None = None,
    db: Session = Depends(get_session),
) -> SessionOut:
    if body and body.sessionId:
        existing = db.get(ChatSession, body.sessionId)
        if existing is None:
            raise AppError("That study session was not found.", status_code=404)
        existing.updated_at = utcnow()
        db.add(existing)
        db.commit()
        db.refresh(existing)
        return session_out(existing)
    title = (body.title.strip() if body and body.title else "") or "New study session"
    chat = ChatSession(id=str(uuid.uuid4()), title=title[:120], created_at=utcnow(), updated_at=utcnow())
    db.add(chat)
    db.commit()
    db.refresh(chat)
    await _create_adk_session(chat.id)
    return session_out(chat)


@router.get("/sessions/{session_id}", response_model=SessionDetail)
def get_session_detail(session_id: str, db: Session = Depends(get_session)) -> SessionDetail:
    chat = db.get(ChatSession, session_id)
    if chat is None:
        raise AppError("That study session was not found.", status_code=404)
    messages = db.exec(
        select(Message).where(Message.session_id == session_id).order_by(Message.created_at)
    ).all()
    artifacts = db.exec(
        select(Artifact).where(Artifact.session_id == session_id).order_by(Artifact.created_at)
    ).all()
    return SessionDetail(
        session=session_out(chat),
        messages=[message_out(item) for item in messages],
        artifacts=[artifact_out(item) for item in artifacts],
    )


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str, db: Session = Depends(get_session)) -> Response:
    chat = db.get(ChatSession, session_id)
    if chat is None:
        raise AppError("That study session was not found.", status_code=404)
    for message in db.exec(select(Message).where(Message.session_id == session_id)).all():
        db.delete(message)
    for artifact in db.exec(select(Artifact).where(Artifact.session_id == session_id)).all():
        db.delete(artifact)
    db.delete(chat)
    db.commit()
    await _delete_adk_session(session_id)
    return Response(status_code=204)


async def _create_adk_session(session_id: str) -> None:
    import logging

    logger = logging.getLogger("educator.agent")
    service = runtime.session_service
    if service is None:
        return
    try:
        created = service.create_session(app_name=APP_NAME, user_id=USER_ID, session_id=session_id)
    except TypeError:
        logger.error("ADK session service does not accept session_id; refusing orphan session")
        raise
    if inspect.isawaitable(created):
        await created


async def _delete_adk_session(session_id: str) -> None:
    service = runtime.session_service
    if service is None:
        return
    deleter = getattr(service, "delete_session", None)
    if deleter is None:
        return
    try:
        result = deleter(app_name=APP_NAME, user_id=USER_ID, session_id=session_id)
        if inspect.isawaitable(result):
            await result
    except Exception:
        return
