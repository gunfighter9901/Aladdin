"""Gemini calls for transcription, structured study artifacts, and multi-speaker TTS.

The agent itself is Google ADK. This client is for ingestion and the studio
pipelines, which are also the implementations behind the agent's tools.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import wave
from collections.abc import Awaitable, Callable
from typing import Any

from app.config import settings
from app.errors import GeminiUnavailable
from app.safety import safe_error_message

from opik import track

logger = logging.getLogger("educator.gemini")


async def _with_retry(label: str, call: Callable[[], Awaitable[Any]]) -> Any:
    """Run one Gemini call with a timeout and a single retry on transient failures."""
    timeout = max(5.0, float(settings.gemini_timeout_s))
    last: Exception | None = None
    for attempt in (1, 2):
        try:
            return await asyncio.wait_for(call(), timeout=timeout)
        except (TimeoutError, asyncio.TimeoutError) as exc:
            last = exc
            logger.warning("Gemini %s timed out (attempt %s)", label, attempt)
        except GeminiUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - mapped to a safe message below
            message = safe_error_message(exc)
            if attempt == 2 or "rejected" in message.lower() or "api key" in message.lower():
                raise GeminiUnavailable(message) from exc
            last = exc
            logger.warning("Gemini %s failed transiently (attempt %s)", label, attempt)
    raise GeminiUnavailable(safe_error_message(last) if last else "The model service is unavailable.") from last

QUIZ_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "question": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                    "correctAnswer": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["id", "question", "options", "correctAnswer", "rationale"],
            },
        },
        "flashcards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "front": {"type": "string"},
                    "back": {"type": "string"},
                },
                "required": ["id", "front", "back"],
            },
        },
    },
    "required": ["title", "questions", "flashcards"],
}

SCRIPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "turns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "speaker": {"type": "string", "enum": ["Joe", "Jane"]},
                    "text": {"type": "string"},
                },
                "required": ["speaker", "text"],
            },
        },
    },
    "required": ["title", "turns"],
}


class GeminiClient:
    def __init__(self) -> None:
        self._client = None
        if not settings.gemini_configured():
            return
        from google import genai

        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self._client = genai.Client(api_key=api_key)

    @property
    def configured(self) -> bool:
        return self._client is not None

    def require(self) -> Any:
        if self._client is None:
            raise GeminiUnavailable()
        return self._client

    async def generate_text(self, prompt: str, *, temperature: float = 0.4) -> str:
        return await _llm_text(self, prompt, temperature=temperature)

    async def generate_json(self, prompt: str, schema: dict[str, Any], *, temperature: float = 0.3) -> dict[str, Any]:
        return await _llm_json(self, prompt, schema, temperature=temperature)

    async def transcribe(self, data: bytes, mime_type: str, prompt: str) -> str:
        # Audio/image bytes are large; log only sizes in the span.
        return await _llm_transcribe(self, len(data), mime_type, data, prompt)

    async def synthesize_podcast(self, dialogue: str) -> tuple[str, str]:
        """Return (base64 wav, mime type) for 24 kHz multi-speaker speech."""
        return await _llm_tts(self, dialogue)


def _as_schema(types: Any, schema: dict[str, Any]) -> Any:
    """Build a google.genai Schema using Type.OBJECT, as the quiz pipeline requires."""
    type_name = str(schema.get("type", "object")).upper()
    kwargs: dict[str, Any] = {"type": getattr(types.Type, type_name)}
    if "enum" in schema:
        kwargs["enum"] = schema["enum"]
    properties = schema.get("properties")
    if properties:
        kwargs["properties"] = {key: _as_schema(types, value) for key, value in properties.items()}
    if "items" in schema:
        kwargs["items"] = _as_schema(types, schema["items"])
    if "required" in schema:
        kwargs["required"] = schema["required"]
    return types.Schema(**kwargs)


def _media_part(types: Any, data: bytes, mime_type: str) -> Any:
    if hasattr(types.Part, "from_bytes"):
        return types.Part.from_bytes(data=data, mime_type=mime_type)
    return types.Part(inline_data=types.Blob(mime_type=mime_type, data=data))


def _extract_audio(response: Any) -> bytes:
    audios = _extract_audios(response)
    return audios[0] if audios else b""


def _extract_audios(response: Any) -> list[bytes]:
    """Collect every audio blob in a generate_content response.

    Handles SDK shape drift: ``inline_data`` (Blob with bytes or base64 str),
    camelCase ``inlineData`` dicts, and legacy ``blob`` fields. Text-only
    parts are skipped. Never raises; returns [] when no audio is present.
    """
    out: list[bytes] = []
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            data = _part_audio_data(part)
            if data:
                out.append(data)
    return out


def _part_audio_data(part: Any) -> bytes:
    candidates: list[Any] = []
    if isinstance(part, dict):
        candidates.extend(
            [
                part.get("inline_data"),
                part.get("inlineData"),
                part.get("inline_data", {}).get("data") if isinstance(part.get("inline_data"), dict) else None,
                part.get("blob"),
            ]
        )
    else:
        inline = getattr(part, "inline_data", None)
        candidates.append(inline)
        # Some SDK builds expose camelCase on the model dump only.
        try:
            dump = part.model_dump() if hasattr(part, "model_dump") else None
        except Exception:
            dump = None
        if isinstance(dump, dict):
            candidates.extend([dump.get("inlineData"), dump.get("blob")])
        candidates.append(getattr(part, "blob", None))
    for item in candidates:
        raw = item
        if raw is not None and not isinstance(raw, (bytes, str)):
            raw = getattr(raw, "data", None)
        if not raw:
            continue
        try:
            if isinstance(raw, str):
                return base64.b64decode(raw)
            return bytes(raw)
        except Exception:
            continue
    return b""


def _describe_response(response: Any, *, limit: int = 500) -> dict[str, Any]:
    """Small structural summary for Opik/logs. Never includes audio bytes."""
    try:
        candidates = getattr(response, "candidates", None) or []
        parts_info: list[dict[str, Any]] = []
        text_preview = ""
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", None) or []:
                keys: list[str] = []
                try:
                    if hasattr(part, "model_dump"):
                        dumped = part.model_dump()
                        keys = sorted(str(k) for k in dumped.keys()) if isinstance(dumped, dict) else []
                    elif isinstance(part, dict):
                        keys = sorted(str(k) for k in part.keys())
                    else:
                        keys = sorted(k for k in vars(part).keys() if not k.startswith("_"))
                except Exception:
                    pass
                inline = getattr(part, "inline_data", None) if not isinstance(part, dict) else None
                mime = getattr(inline, "mime_type", None) if inline is not None else None
                text = getattr(part, "text", None) if not isinstance(part, dict) else part.get("text")
                if text and not text_preview:
                    text_preview = str(text)[:limit]
                parts_info.append({"keys": keys[:8], "mime": str(mime)[:80] if mime else None, "has_text": bool(text)})
        feedback = getattr(response, "prompt_feedback", None)
        return {
            "candidates": len(candidates),
            "parts": parts_info[:6],
            "text_preview": text_preview[:limit],
            "prompt_feedback": str(feedback)[:300] if feedback else None,
        }
    except Exception:
        return {"candidates": "unknown"}


def _pcm_to_wav(pcm: bytes, rate: int = 24000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm)
    return buffer.getvalue()


def _to_pcm(data: bytes) -> bytes:
    """Normalize a TTS chunk to raw 16-bit PCM. Strips WAV/RIFF headers when present."""
    if not data:
        return b""
    if data.startswith(b"RIFF"):
        try:
            with wave.open(io.BytesIO(data), "rb") as handle:
                return handle.readframes(handle.getnframes())
        except Exception:
            # Fall back to dumb 44-byte header strip; never fail the whole podcast.
            return data[44:] if len(data) > 44 else b""
    return data


def _split_dialogue_chunks(dialogue: str, *, max_chars: int = 1500) -> list[str]:
    """Split 'Speaker: text' dialogue into TTS-safe chunks at turn boundaries."""
    lines = [line.strip() for line in (dialogue or "").splitlines() if line.strip()]
    if not lines:
        return []
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in lines:
        if current and size + len(line) + 1 > max_chars:
            chunks.append("\n".join(current))
            current = [line]
            size = len(line)
        else:
            current.append(line)
            size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def _strip_fence(text: str) -> str:
    raw = text.strip()
    if not raw.startswith("```"):
        return raw
    lines = raw.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


@track(name="gemini_text", type="llm", ignore_arguments=["client"])
async def _llm_text(client: GeminiClient, prompt: str, *, temperature: float) -> str:
    from app.observability import truncate, update_span

    raw = client.require()

    async def _call() -> Any:
        return await raw.aio.models.generate_content(
            model=settings.reasoning_model,
            contents=prompt,
            config={"temperature": temperature},
        )

    try:
        response = await _with_retry("text", _call)
    except GeminiUnavailable:
        raise
    except Exception as exc:
        raise GeminiUnavailable(safe_error_message(exc)) from exc
    text = (getattr(response, "text", None) or "").strip()
    if not text:
        raise GeminiUnavailable("The model returned an empty response.")
    update_span(
        input={"prompt": truncate(prompt, 2000), "temperature": temperature},
        output={"text": truncate(text, 2000)},
        metadata={"model": settings.reasoning_model, "provider": "google"},
        model=settings.reasoning_model,
    )
    return text


@track(name="gemini_json", type="llm", ignore_arguments=["client"])
async def _llm_json(
    client: GeminiClient, prompt: str, schema: dict[str, Any], *, temperature: float
) -> dict[str, Any]:
    from app.observability import truncate, update_span

    raw_client = client.require()
    from google.genai import types

    config = types.GenerateContentConfig(
        temperature=temperature,
        response_mime_type="application/json",
        response_schema=_as_schema(types, schema),
    )

    async def _call() -> Any:
        return await raw_client.aio.models.generate_content(
            model=settings.reasoning_model,
            contents=prompt,
            config=config,
        )

    try:
        response = await _with_retry("json", _call)
    except GeminiUnavailable:
        raise
    except Exception as exc:
        raise GeminiUnavailable(safe_error_message(exc)) from exc
    raw = (getattr(response, "text", None) or "").strip()
    if not raw:
        raise GeminiUnavailable("The model returned an empty structured response.")
    try:
        parsed = json.loads(_strip_fence(raw))
    except json.JSONDecodeError as exc:
        raise GeminiUnavailable("The model did not return valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise GeminiUnavailable("The model did not return a JSON object.")
    update_span(
        input={"prompt": truncate(prompt, 2000), "temperature": temperature},
        output={"keys": sorted(parsed.keys())},
        metadata={"model": settings.reasoning_model, "provider": "google"},
        model=settings.reasoning_model,
    )
    return parsed


@track(name="gemini_transcribe", type="llm", ignore_arguments=["client", "data"])
async def _llm_transcribe(
    client: GeminiClient, size: int, mime_type: str, data: bytes, prompt: str
) -> str:
    from app.observability import truncate, update_span

    raw = client.require()
    from google.genai import types

    part = _media_part(types, data, mime_type)

    async def _call() -> Any:
        return await raw.aio.models.generate_content(
            model=settings.reasoning_model,
            contents=[part, prompt],
            config={"temperature": 0.2},
        )

    try:
        response = await _with_retry("transcribe", _call)
    except GeminiUnavailable:
        raise
    except Exception as exc:
        raise GeminiUnavailable(safe_error_message(exc)) from exc
    text = (getattr(response, "text", None) or "").strip()
    if not text:
        raise GeminiUnavailable("No text could be transcribed from that file.")
    update_span(
        input={"bytes": size, "mime_type": mime_type, "prompt": truncate(prompt, 500)},
        output={"chars": len(text), "preview": truncate(text, 1000)},
        metadata={"model": settings.reasoning_model, "provider": "google"},
        model=settings.reasoning_model,
    )
    return text


@track(name="gemini_tts", type="llm", capture_output=False, ignore_arguments=["client"])
async def _llm_tts(client: GeminiClient, dialogue: str) -> tuple[str, str]:
    """TTS audio is large; the span logs sizes only, never base64.

    Long dialogues are synthesized per chunk and stitched as PCM so one slow
    chunk cannot sink the whole podcast. Specific failures raise
    GeminiUnavailable with the model name + response shape (preserved verbatim
    by safe_error_message), so Opik audio_error and the UI hint say WHY.
    """
    from app.observability import truncate, update_span

    raw = client.require()
    from google.genai import types

    if not (dialogue or "").strip():
        raise GeminiUnavailable("There was no podcast script to speak.")
    model = settings.tts_model
    chunks = _split_dialogue_chunks(dialogue) or [dialogue]

    def _config() -> Any:
        return types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                multi_speaker_voice_config=types.MultiSpeakerVoiceConfig(
                    speaker_voice_configs=[
                        types.SpeakerVoiceConfig(
                            speaker="Joe",
                            voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Puck")
                            ),
                        ),
                        types.SpeakerVoiceConfig(
                            speaker="Jane",
                            voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Kore")
                            ),
                        ),
                    ]
                )
            ),
        )

    async def _one_chunk(text: str) -> bytes:
        config = _config()

        async def _call() -> Any:
            return await raw.aio.models.generate_content(
                model=model,
                contents=text,
                config=config,
            )

        try:
            response = await _with_retry("tts", _call)
        except GeminiUnavailable as exc:
            # _with_retry collapses unknown errors to a generic message via
            # safe_error_message. Unwrap the cause so Opik/UI keep the model
            # name + real reason (404 model, 400 speech config, quota...).
            cause = exc.__cause__
            if (exc.message or "").strip() in (
                "Something went wrong on the server. Try again in a moment.",
                "The model service is unavailable.",
            ) and cause is not None:
                raise _tts_error(model, cause) from cause
            raise
        except Exception as exc:
            raise _tts_error(model, exc) from exc
        audios = _extract_audios(response)
        if not audios:
            raise GeminiUnavailable(
                f"TTS model {model} returned no audio "
                f"(candidates={_describe_response(response).get('candidates')}). "
                "The script is still shown; audio was not returned."
            )
        pcm_parts = [_to_pcm(a) for a in audios]
        pcm_parts = [p for p in pcm_parts if p]
        if not pcm_parts:
            raise GeminiUnavailable(
                f"TTS model {model} returned empty audio. The script is still shown."
            )
        return b"".join(pcm_parts)

    pcm_all: list[bytes] = []
    failures = 0
    last_error: Exception | None = None
    for index, text in enumerate(chunks):
        try:
            pcm_all.append(await _one_chunk(text))
        except GeminiUnavailable as exc:
            failures += 1
            last_error = exc
            logger.warning("Gemini TTS chunk %s/%s failed: %s", index + 1, len(chunks), exc.message[:300])
        except Exception as exc:  # noqa: BLE001 - converted to a specific error below
            failures += 1
            last_error = exc
            logger.warning("Gemini TTS chunk %s/%s failed: %s", index + 1, len(chunks), type(exc).__name__)
    if not pcm_all:
        if isinstance(last_error, GeminiUnavailable):
            raise last_error
        raise _tts_error(model, last_error) from last_error
    wav = _pcm_to_wav(b"".join(pcm_all))
    encoded = base64.b64encode(wav).decode("ascii")
    partial = f" ({failures}/{len(chunks)} chunks failed)" if failures else ""
    update_span(
        input={"dialogue_chars": len(dialogue), "preview": truncate(dialogue, 1000), "chunks": len(chunks)},
        output={"audio_bytes": len(wav), "mime": "audio/wav", "failed_chunks": failures},
        metadata={"model": model, "provider": "google", "voices": {"Joe": "Puck", "Jane": "Kore"}},
        model=model,
    )
    if failures:
        logger.warning("Gemini TTS partial audio%s using model %s", partial, model)
    return encoded, "audio/wav"


def _tts_error(model: str, exc: BaseException | None) -> GeminiUnavailable:
    """Map a raw TTS failure to a specific, user-safe message with the model name."""
    from app.safety import redact

    raw = redact(str(exc)).strip() if exc is not None else ""
    lowered = raw.lower()
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or "timed out" in lowered or "timeout" in lowered:
        return GeminiUnavailable(
            f"TTS model {model} timed out speaking a long script. "
            "The script is still shown; try a shorter focus to get audio."
        )
    if "429" in raw or "resource_exhausted" in lowered or "quota" in lowered:
        return GeminiUnavailable("The speech service is rate-limited right now. The script is still shown; try audio again shortly.")
    if "api key" in lowered or "permission_denied" in lowered or "unauthenticated" in lowered:
        return GeminiUnavailable("The Gemini API key was rejected. Check GEMINI_API_KEY in backend/.env.")
    if "404" in raw or "not_found" in lowered or "not found" in lowered:
        return GeminiUnavailable(
            f"TTS model {model} was not found for this API key. The script is still shown; "
            "check the model name and key permissions."
        )
    if "400" in raw or "invalid" in lowered or "speech" in lowered:
        detail = raw[:200] if raw else "the request was rejected"
        return GeminiUnavailable(
            f"TTS model {model} rejected the request ({detail}). The script is still shown."
        )
    if "503" in raw or "overloaded" in lowered or "unavailable" in lowered:
        return GeminiUnavailable("The speech service is temporarily unavailable. The script is still shown; try again shortly.")
    detail = raw[:200] if raw else "the service failed"
    return GeminiUnavailable(f"Speech synthesis via {model} failed ({detail}). The script is still shown.")


_client: GeminiClient | None = None


def get_gemini() -> GeminiClient:
    global _client
    if _client is None:
        _client = GeminiClient()
    return _client
