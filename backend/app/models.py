"""App tables. Agent event history lives in the ADK SQLite session store."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Source(SQLModel, table=True):
    __tablename__ = "sources"

    id: str = Field(primary_key=True)
    filename: str = Field(index=True)
    title: str
    kind: str = Field(index=True)
    origin: str = ""
    summary: str = ""
    chunk_count: int = 0
    active: bool = True
    status: str = Field(default="processing", index=True)
    error: str | None = None
    mime_type: str | None = None
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)


class ChatSession(SQLModel, table=True):
    __tablename__ = "chat_sessions"

    id: str = Field(primary_key=True)
    title: str = "New study session"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Message(SQLModel, table=True):
    __tablename__ = "messages"

    id: str = Field(primary_key=True)
    session_id: str = Field(index=True, foreign_key="chat_sessions.id", ondelete="CASCADE")
    role: str = Field(index=True)
    content: str
    citations_json: str = "[]"
    trace_json: str = "[]"
    artifacts_json: str = "{}"
    created_at: datetime = Field(default_factory=utcnow)


class Artifact(SQLModel, table=True):
    __tablename__ = "artifacts"

    id: str = Field(primary_key=True)
    session_id: str | None = Field(default=None, index=True)
    kind: str = Field(index=True)
    title: str = ""
    payload_json: str
    created_at: datetime = Field(default_factory=utcnow)
