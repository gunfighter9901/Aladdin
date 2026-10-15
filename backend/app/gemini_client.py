"""Gemini calls for transcription, structured study artifacts, and multi-speaker TTS.

The agent itself is Google ADK. This client is for ingestion and the studio
pipelines, which are also the implementations behind the agent's tools.
"""

from __future__ import annotations

import base64
import io
import json
import os
import wave
from typing import Any

from app.config import settings
from app.errors import GeminiUnavailable
from app.safety import safe_error_message

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
        client = self.require()
        try:
            response = await client.aio.models.generate_content(
                model=settings.reasoning_model,
                contents=prompt,
                config={"temperature": temperature},
            )
        except Exception as exc:
            raise GeminiUnavailable(safe_error_message(exc)) from exc
        text = (getattr(response, "text", None) or "").strip()
        if not text:
            raise GeminiUnavailable("The model returned an empty response.")
        return text

    async def generate_json(self, prompt: str, schema: dict[str, Any], *, temperature: float = 0.3) -> dict[str, Any]:
        client = self.require()
        from google.genai import types

        config = types.GenerateContentConfig(
            temperature=temperature,
            response_mime_type="application/json",
            response_schema=_as_schema(types, schema),
        )
        try:
            response = await client.aio.models.generate_content(
                model=settings.reasoning_model,
                contents=prompt,
                config=config,
            )
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
        return parsed

    async def transcribe(self, data: bytes, mime_type: str, prompt: str) -> str:
        client = self.require()
        from google.genai import types

        part = _media_part(types, data, mime_type)
        try:
            response = await client.aio.models.generate_content(
                model=settings.reasoning_model,
                contents=[part, prompt],
                config={"temperature": 0.2},
            )
        except Exception as exc:
            raise GeminiUnavailable(safe_error_message(exc)) from exc
        text = (getattr(response, "text", None) or "").strip()
        if not text:
            raise GeminiUnavailable("No text could be transcribed from that file.")
        return text

    async def synthesize_podcast(self, dialogue: str) -> tuple[str, str]:
        """Return (base64 wav, mime type) for 24 kHz multi-speaker speech."""
        client = self.require()
        from google.genai import types

        config = types.GenerateContentConfig(
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
        try:
            response = await client.aio.models.generate_content(
                model=settings.tts_model,
                contents=dialogue,
                config=config,
            )
        except Exception as exc:
            raise GeminiUnavailable(safe_error_message(exc)) from exc
        audio = _extract_audio(response)
        if not audio:
            raise GeminiUnavailable("Speech synthesis did not return audio.")
        wav = audio if audio.startswith(b"RIFF") else _pcm_to_wav(audio)
        encoded = base64.b64encode(wav).decode("ascii")
        return encoded, "audio/wav"


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
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            inline = getattr(part, "inline_data", None)
            if inline is None:
                continue
            data = getattr(inline, "data", None)
            if not data:
                continue
            if isinstance(data, str):
                return base64.b64decode(data)
            return bytes(data)
    return b""


def _pcm_to_wav(pcm: bytes, rate: int = 24000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm)
    return buffer.getvalue()


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


_client: GeminiClient | None = None


def get_gemini() -> GeminiClient:
    global _client
    if _client is None:
        _client = GeminiClient()
    return _client
