"""Podcast, quiz, and Mermaid pipelines. Shared by the API and the ADK tools."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.errors import AppError, GeminiUnavailable
from app.gemini_client import SCRIPT_SCHEMA, QUIZ_SCHEMA, get_gemini
from app.observability import chunk_preview, truncate, update_span
from app.vectorstore import Hit, get_store

from opik import track

MERMAID_STARTS = (
    "mindmap",
    "flowchart",
    "graph ",
    "graph\n",
    "timeline",
    "sequenceDiagram",
)
DIAGRAM_HINTS = {
    "mindmap": "a mindmap",
    "flowchart": "a flowchart",
    "timeline": "a timeline",
    "sequence": "a sequenceDiagram",
}


class QuizQuestion(BaseModel):
    id: str
    question: str
    options: list[str] = Field(min_length=2)
    correctAnswer: str
    rationale: str


class Flashcard(BaseModel):
    id: str
    front: str
    back: str


class QuizPayload(BaseModel):
    title: str
    questions: list[QuizQuestion] = Field(min_length=1)
    flashcards: list[Flashcard] = Field(min_length=1)


class ScriptTurn(BaseModel):
    speaker: str
    text: str


class ScriptPayload(BaseModel):
    title: str
    turns: list[ScriptTurn] = Field(min_length=2)


async def generate_podcast(source_ids: list[str], focus: str = "") -> dict[str, Any]:
    return await _podcast_pipeline(list(source_ids or []), focus or "")


@track(name="podcast_pipeline", type="tool", capture_output=False)
async def _podcast_pipeline(source_ids: list[str], focus: str) -> dict[str, Any]:
    context, _hits = await _study_context(source_ids, focus)
    topic = focus.strip() or "the main ideas a student should remember"
    prompt = f"""
Write a short study podcast for a student. Host "Joe" asks curious questions.
Expert "Jane" explains, using only the source excerpts below. Do not invent facts,
page numbers, or citations that are not in the excerpts.

Topic focus: {topic}
Length: 8 to 12 turns. Alternate speakers, starting with Joe.
Each turn is one or two spoken sentences. No stage directions. No markdown.

Source excerpts:
{context}
""".strip()
    raw = await get_gemini().generate_json(prompt, SCRIPT_SCHEMA, temperature=0.5)
    try:
        script = ScriptPayload.model_validate(raw)
    except ValidationError as exc:
        raise GeminiUnavailable("The podcast script was not in the expected shape.") from exc
    turns = []
    for turn in script.turns:
        speaker = "Jane" if turn.speaker.strip().lower() == "jane" else "Joe"
        text = " ".join(turn.text.split())
        if text:
            turns.append({"speaker": speaker, "text": text})
    if len(turns) < 2:
        raise GeminiUnavailable("The podcast script did not include both speakers.")
    # Truncate at a turn boundary so TTS never receives a half-sentence.
    # Keep whole "Speaker: text" lines within the byte budget.
    dialogue_lines: list[str] = []
    budget = 6000
    for turn in turns:
        line = f"{turn['speaker']}: {turn['text']}"
        if len("\n".join(dialogue_lines + [line])) > budget:
            break
        dialogue_lines.append(line)
    truncated = len(dialogue_lines) < len(turns)
    # Always keep at least the first two turns so both voices are heard.
    if len(dialogue_lines) < 2:
        dialogue_lines = [f"{turn['speaker']}: {turn['text']}" for turn in turns[:2]]
        truncated = len(turns) > 2
    dialogue = "\n".join(dialogue_lines)
    audio_b64 = ""
    mime = "audio/wav"
    audio_error = None
    try:
        audio_b64, mime = await get_gemini().synthesize_podcast(dialogue)
    except GeminiUnavailable as exc:
        audio_error = exc.message
    update_span(
        input={"source_ids": source_ids, "focus": truncate(focus, 1000)},
        output={
            "title": script.title.strip() or "Study podcast",
            "turn_count": len(turns),
            "audio_ready": bool(audio_b64),
        },
        metadata={"audio_error": audio_error},
    )
    return {
        "title": script.title.strip() or "Study podcast",
        "script": dialogue,
        "turns": turns,
        "audioBase64": audio_b64,
        "mimeType": mime,
        "sampleRateHz": 24000,
        "voices": {"Joe": "Puck", "Jane": "Kore"},
        "audioError": audio_error,
        "truncated": truncated,
    }


async def generate_quiz(source_ids: list[str], focus: str = "", count: int = 5) -> dict[str, Any]:
    return await _quiz_pipeline(list(source_ids or []), focus or "", count)


@track(name="quiz_pipeline", type="tool")
async def _quiz_pipeline(source_ids: list[str], focus: str, count: int) -> dict[str, Any]:
    context, _hits = await _study_context(source_ids, focus)
    topic = focus.strip() or "the ideas that matter most in these excerpts"
    prompt = f"""
Build an active-recall quiz for a student from the excerpts only.
Do not ask about facts that are not in the excerpts.
Write {count} multiple-choice questions with exactly 4 options each.
correctAnswer must be one of the options, copied exactly.
rationale must explain the answer from the material in one or two sentences.
Also write {min(count, 8)} flashcards. front is a prompt, back is the answer.

Topic focus: {topic}

Source excerpts:
{context}
""".strip()
    raw = await get_gemini().generate_json(prompt, QUIZ_SCHEMA, temperature=0.3)
    try:
        quiz = QuizPayload.model_validate(raw)
    except ValidationError as exc:
        raise GeminiUnavailable("The quiz was not in the expected shape.") from exc
    questions = []
    for index, item in enumerate(quiz.questions[:count], start=1):
        options = [option.strip() for option in item.options if option.strip()]
        answer = item.correctAnswer.strip()
        if answer not in options:
            continue
        questions.append(
            {
                "id": item.id.strip() or f"q{index}",
                "question": item.question.strip(),
                "options": options,
                "correctAnswer": answer,
                "rationale": item.rationale.strip(),
            }
        )
    if not questions:
        raise GeminiUnavailable("The quiz did not include a usable question.")
    flashcards = [
        {"id": card.id.strip() or f"f{index}", "front": card.front.strip(), "back": card.back.strip()}
        for index, card in enumerate(quiz.flashcards, start=1)
        if card.front.strip() and card.back.strip()
    ]
    update_span(
        input={"source_ids": source_ids, "focus": truncate(focus, 1000), "count": count},
        output={"title": quiz.title.strip() or "Active recall", "question_count": len(questions)},
    )
    return {
        "title": quiz.title.strip() or "Active recall",
        "questions": questions,
        "flashcards": flashcards,
        "requestedCount": count,
        "returnedCount": len(questions),
    }


async def generate_infographic(
    source_ids: list[str],
    focus: str = "",
    diagram_type: str = "mindmap",
) -> dict[str, Any]:
    return await _infographic_pipeline(list(source_ids or []), focus or "", diagram_type or "mindmap")


@track(name="infographic_pipeline", type="tool")
async def _infographic_pipeline(source_ids: list[str], focus: str, diagram_type: str) -> dict[str, Any]:
    context, _hits = await _study_context(source_ids, focus)
    kind = diagram_type if diagram_type in DIAGRAM_HINTS else "mindmap"
    hint = DIAGRAM_HINTS[kind]
    topic = focus.strip() or "the central concept in these excerpts"
    prompt = f"""
Create {hint} in Mermaid.js that helps a student see how the ideas connect.
Use only concepts present in the excerpts. Do not invent sources.

Return only the Mermaid markup. No code fences. No explanation.
The first line must be a valid Mermaid diagram type.
For a mindmap, start with `mindmap`.
For a flowchart, start with `flowchart TD`.
For a timeline, start with `timeline`.
For a sequence diagram, start with `sequenceDiagram`.
Keep node labels short. Do not use parentheses inside mindmap nodes.

Topic focus: {topic}

Source excerpts:
{context}
""".strip()
    mermaid = _clean_mermaid(await get_gemini().generate_text(prompt, temperature=0.3))
    if not _valid_mermaid(mermaid):
        repair = await get_gemini().generate_text(
            "Rewrite the following as valid Mermaid only. No fences.\n\n" + mermaid,
            temperature=0.1,
        )
        mermaid = _clean_mermaid(repair)
    if not _valid_mermaid(mermaid):
        raise GeminiUnavailable("The diagram was not valid Mermaid markup.")
    title = topic[:80] if focus.strip() else f"{kind.title()} from your sources"
    update_span(
        input={"source_ids": source_ids, "focus": truncate(focus, 1000), "diagram_type": kind},
        output={"title": title, "diagram_type": kind},
        metadata={"mermaid": truncate(mermaid, 2000)},
    )
    return {"title": title, "diagramType": kind, "mermaid": mermaid}


async def summarize_source(filename: str, text: str) -> str:
    return await _summarize_span(filename, text)


@track(name="summarize_source", type="tool")
async def _summarize_span(filename: str, text: str) -> str:
    excerpt = text.strip()[:8000]
    if not excerpt:
        return ""
    if not get_gemini().configured:
        return _extractive_summary(excerpt)
    prompt = (
        "Write a 2-3 sentence study summary of this source. "
        "Use only the text below. Do not invent facts.\n\n"
        f"Filename: {filename}\n\n{excerpt}"
    )
    try:
        return await get_gemini().generate_text(prompt, temperature=0.2)
    except GeminiUnavailable:
        return _extractive_summary(excerpt)


async def _study_context(source_ids: list[str], focus: str) -> tuple[str, list[Hit]]:
    return await _retrieve_study_context(list(source_ids or []), focus or "")


@track(name="retrieve_study_context", type="tool")
async def _retrieve_study_context(source_ids: list[str], focus: str) -> tuple[str, list[Hit]]:
    ids = [item for item in source_ids if item]
    if not ids:
        raise AppError("Select at least one source. An empty selection retrieves nothing.")
    store = get_store()
    hits: list[Hit] = []
    fallback_used = False
    if focus.strip():
        hits = await asyncio.to_thread(store.search, focus.strip(), ids, 8)
    if not hits:
        hits = await asyncio.to_thread(store.sample, ids, 8)
        fallback_used = bool(hits and focus.strip())
    if not hits:
        raise AppError("The selected materials do not contain retrievable text.", status_code=404)
    # Dedup identical chunk payloads that can repeat across overlapping windows.
    seen_texts: set[str] = set()
    unique: list[Hit] = []
    for hit in hits:
        key = f"{hit.source_id}\x00{hit.chunk_id}\x00{hit.text}"
        if key in seen_texts:
            continue
        seen_texts.add(key)
        unique.append(hit)
    hits = unique
    blocks = []
    for hit in hits:
        page = f", page {hit.page_number}" if hit.page_number else ""
        blocks.append(f"[Source: {hit.filename}, Chunk: {hit.chunk_id}{page}]\n{hit.text}")
    context = _truncate_at_sentence("\n\n".join(blocks), 14000)
    update_span(
        input={"source_ids": ids, "focus": truncate(focus, 1000)},
        output={"hit_count": len(hits), "context_chars": len(context), "fallback_used": fallback_used},
        metadata={"chunks": chunk_preview(hits)},
    )
    return context, hits


def _truncate_at_sentence(text: str, limit: int) -> str:
    """Cut at a sentence/paragraph boundary so context never ends mid-word."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in ("\n\n", "\n", ". ", "! ", "? "):
        idx = cut.rfind(sep)
        if idx > limit * 0.6:
            return cut[: idx + len(sep)].strip()
    # Fall back to last space to avoid splitting a token the embedder counts.
    space = cut.rfind(" ")
    return (cut[:space] if space > limit * 0.6 else cut).strip()


def _extractive_summary(text: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", " ".join(text.split()))
    picked = " ".join(sentences[:3]).strip()
    return picked[:500]


def _clean_mermaid(text: str) -> str:
    raw = text.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    return raw


def _valid_mermaid(text: str) -> bool:
    raw = (text or "").strip()
    if not raw or "```" in raw or len(raw) > 20000:
        return False
    lines = [line for line in raw.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    first = lines[0].strip()
    if not any(first.startswith(prefix) for prefix in MERMAID_STARTS):
        return False
    # Reject unbalanced brackets that always fail to render.
    for open_c, close_c in (("[", "]"), ("{", "}"), ("(", ")")):
        if raw.count(open_c) != raw.count(close_c):
            return False
    return True
