"""Request and response contracts shared with the React client."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class SourceOut(BaseModel):
    id: str
    filename: str
    title: str
    kind: str
    origin: str
    summary: str
    chunkCount: int
    active: bool
    status: str
    error: str | None = None
    createdAt: datetime


class SourceUpdate(BaseModel):
    active: bool | None = None
    title: str | None = Field(default=None, max_length=180)


class CollectionOut(BaseModel):
    name: str
    points: int
    embeddingModel: str
    isolation: str
    quantization: str
    payload: str


class SessionOut(BaseModel):
    id: str
    title: str
    createdAt: datetime
    updatedAt: datetime


class SessionCreate(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    sessionId: str | None = Field(default=None, max_length=64)


class CitationOut(BaseModel):
    sourceId: str
    filename: str
    chunkId: str
    pageNumber: int | None = None


class TraceStep(BaseModel):
    type: Literal["status", "tool_call", "tool_result", "response"]
    name: str | None = None
    text: str | None = None
    args: Any = None
    result: Any = None


class ChatRequest(BaseModel):
    sessionId: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=8000)
    activeSourceIds: list[str] = Field(default_factory=list, max_length=64)


class ChatResponse(BaseModel):
    sessionId: str
    messageId: str
    content: str
    citations: list[CitationOut] = Field(default_factory=list)
    trace: list[TraceStep] = Field(default_factory=list)
    artifacts: dict[str, Any] = Field(default_factory=dict)


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    citations: list[CitationOut] = Field(default_factory=list)
    trace: list[TraceStep] = Field(default_factory=list)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    createdAt: datetime


class ArtifactOut(BaseModel):
    id: str
    kind: str
    title: str
    payload: dict[str, Any]
    createdAt: datetime


class SessionDetail(BaseModel):
    session: SessionOut
    messages: list[MessageOut]
    artifacts: list[ArtifactOut]


class GenerateRequest(BaseModel):
    sourceIds: list[str] = Field(default_factory=list, max_length=64)
    sessionId: str | None = Field(default=None, max_length=64)
    focus: str = Field(default="", max_length=2000)
    count: int = Field(default=5, ge=1, le=12)
    diagramType: Literal["mindmap", "flowchart", "timeline", "sequence"] = "mindmap"


class Pagination(BaseModel):
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class HealthOut(BaseModel):
    status: str
    geminiConfigured: bool
    opikConfigured: bool
    collection: str
    qdrant: str
