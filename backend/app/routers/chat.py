"""Tutor chat. The JSON route is the contract; the stream route feeds the ReAct panel."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.chat_runner import run_turn
from app.errors import AppError
from app.schemas import ChatRequest, ChatResponse

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest) -> ChatResponse:
    done = await _collect(body)
    return ChatResponse.model_validate(done)


@router.post("/chat/stream")
async def chat_stream(body: ChatRequest) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        async for item in run_turn(body.sessionId, body.message, body.activeSourceIds):
            payload = json.dumps(item["data"], default=str)
            yield f"event: {item['event']}\ndata: {payload}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _collect(body: ChatRequest) -> dict:
    done = None
    error = None
    status = 502
    async for item in run_turn(body.sessionId, body.message, body.activeSourceIds):
        if item["event"] == "done":
            done = item["data"]
        elif item["event"] == "error":
            error = item["data"].get("message") or "The tutor could not answer."
            status = int(item["data"].get("status") or status)
    if done:
        return done
    raise AppError(error or "The tutor could not answer.", status_code=status)
