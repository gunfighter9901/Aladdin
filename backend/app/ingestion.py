"""Extract text from files, pages, and transcripts, then chunk and embed."""

from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup
from sqlmodel import Session

from app.config import UPLOAD_DIR, settings
from app.errors import AppError
from app.models import Source, utcnow
from app.safety import assert_public_http_url, safe_error_message, safe_filename
from app.vectorstore import get_store

YOUTUBE_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?.*v=|shorts/|embed/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".webm", ".ogg", ".flac"}
TEXT_EXT = {".txt", ".md", ".markdown"}
MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".webm": "audio/webm",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}


async def ingest_upload(
    session: Session,
    *,
    file_name: str | None,
    file_bytes: bytes | None,
    url: str | None,
    text: str | None,
    title: str | None,
) -> Source:
    provided = [bool(file_bytes), bool(url and url.strip()), bool(text and text.strip())]
    if sum(provided) != 1:
        raise AppError("Provide exactly one of a file, a URL, or pasted text.")
    source_id = str(uuid.uuid4())
    if file_bytes is not None:
        filename = safe_filename(file_name or "upload")
        kind, mime = _kind_for_filename(filename)
        origin = filename
        display = title.strip() if title and title.strip() else filename
    elif url and url.strip():
        clean_url = url.strip()
        if video_id := _youtube_id(clean_url):
            kind, mime = "youtube", None
            filename = _citation_filename(title, f"youtube-{video_id}.txt")
            origin = clean_url
            display = title.strip() if title and title.strip() else filename
        else:
            kind, mime = "url", None
            filename = _citation_filename(title, "web-article.txt")
            origin = clean_url
            display = title.strip() if title and title.strip() else filename
    else:
        kind, mime = "text", "text/plain"
        filename = safe_filename(title.strip() if title and title.strip() else "Pasted notes.txt")
        if Path(filename).suffix.lower() not in TEXT_EXT:
            filename = f"{filename}.txt"
        origin = "pasted text"
        display = title.strip() if title and title.strip() else "Pasted notes"

    source = Source(
        id=source_id,
        filename=filename,
        title=display[:180],
        kind=kind,
        origin=origin[:2000],
        mime_type=mime,
        status="processing",
        active=True,
        created_at=utcnow(),
    )
    session.add(source)
    session.commit()
    session.refresh(source)

    upserted = False
    try:
        pages = await _extract(
            kind=kind,
            filename=filename,
            file_bytes=file_bytes,
            mime_type=mime,
            url=origin if kind in {"url", "youtube"} else None,
            text=text,
        )
        joined = "\n\n".join(page for _, page in pages if page.strip())
        if not joined.strip():
            raise AppError("No text could be extracted from that source.")
        from app.chunking import get_chunker
        from app.generators import summarize_source

        chunks = await asyncio.to_thread(get_chunker().chunk_document, pages)
        if not chunks:
            raise AppError("No text could be extracted from that source.")
        summary = await summarize_source(filename, joined)
        count = await asyncio.to_thread(
            get_store().upsert_chunks,
            source_id=source_id,
            filename=filename,
            chunks=chunks,
        )
        upserted = True
        _store_original(source_id, filename, file_bytes, joined)
        source.summary = summary
        source.chunk_count = count
        source.status = "ready"
        source.error = None
        session.add(source)
        session.commit()
        session.refresh(source)
        return source
    except Exception as exc:
        if upserted:
            # Do not leave orphan Qdrant points when the DB row flips to error
            # after vectors were already written (e.g. disk failure in
            # _store_original). Best-effort cleanup; never masks the original error.
            try:
                await asyncio.to_thread(get_store().delete_source, source_id)
            except Exception:
                pass
        message = exc.message if isinstance(exc, AppError) else safe_error_message(exc)
        source.status = "error"
        source.error = message
        source.active = False
        session.add(source)
        session.commit()
        session.refresh(source)
        if isinstance(exc, AppError):
            raise
        # User-input validation (bad URL, unresolvable host) is a 400, not a 502.
        if isinstance(exc, ValueError):
            raise AppError(message, status_code=400) from exc
        raise AppError(message, status_code=502) from exc


def delete_source_vectors(source_id: str) -> None:
    get_store().delete_source(source_id)


async def _extract(
    *,
    kind: str,
    filename: str,
    file_bytes: bytes | None,
    mime_type: str | None,
    url: str | None,
    text: str | None,
) -> list[tuple[int | None, str]]:
    if kind == "pdf":
        return await asyncio.to_thread(_pdf_pages, file_bytes or b"")
    if kind in {"markdown", "text"}:
        raw = text if text is not None else (file_bytes or b"").decode("utf-8", errors="replace")
        return [(None, raw)]
    if kind == "url":
        return [(None, await _fetch_article(url or ""))]
    if kind == "youtube":
        return [(None, await asyncio.to_thread(_youtube_transcript, url or ""))]
    if kind == "image":
        from app.gemini_client import get_gemini

        transcript = await get_gemini().transcribe(
            file_bytes or b"",
            mime_type or "image/png",
            (
                "Transcribe every visible word in this study image. Describe figures, axes, "
                "and diagrams so a student who cannot see the image can study from the text. "
                "Do not invent content that is not visible."
            ),
        )
        return [(None, transcript)]
    if kind == "audio":
        from app.gemini_client import get_gemini

        transcript = await get_gemini().transcribe(
            file_bytes or b"",
            mime_type or "audio/mpeg",
            (
                "Transcribe this study audio verbatim. If several people speak, label them "
                "Speaker 1, Speaker 2, and so on. Do not summarize and do not add facts."
            ),
        )
        return [(None, transcript)]
    raise AppError(f"Unsupported source type: {kind}")


def _citation_filename(title: str | None, fallback: str) -> str:
    """Filenames appear inside [Source: filename, Chunk: id], so commas are unsafe."""
    name = safe_filename(title.strip()) if title and title.strip() else fallback
    if Path(name).suffix.lower() not in TEXT_EXT | {".pdf"}:
        name = f"{Path(name).stem}.txt"
    return name.replace(",", "")


def _kind_for_filename(filename: str) -> tuple[str, str | None]:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return "pdf", "application/pdf"
    if suffix in {".md", ".markdown"}:
        return "markdown", "text/markdown"
    if suffix == ".txt":
        return "text", "text/plain"
    if suffix in IMAGE_EXT:
        return "image", MIME_BY_EXT[suffix]
    if suffix in AUDIO_EXT:
        return "audio", MIME_BY_EXT[suffix]
    raise AppError("Upload a PDF, Markdown, text, image, or audio file.")


def _pdf_pages(data: bytes) -> list[tuple[int | None, str]]:
    from pypdf import PdfReader
    import io

    reader = PdfReader(io.BytesIO(data))
    pages: list[tuple[int | None, str]] = []
    for index, page in enumerate(reader.pages, start=1):
        extracted = page.extract_text() or ""
        if extracted.strip():
            pages.append((index, extracted))
    return pages


async def _fetch_article(url: str) -> str:
    """Fetch with per-hop SSRF validation, streaming size cap, and content-type check."""
    clean = assert_public_http_url(url)
    headers = {"User-Agent": "EducatorLocal/1.0 (study assistant; local)"}
    current = clean
    async with httpx.AsyncClient(timeout=20, follow_redirects=False, headers=headers) as client:
        final_response = None
        final_url = current
        for _ in range(5):
            # Re-validate every hop so an intermediate redirect to a private
            # address is rejected before the request is issued.
            current = assert_public_http_url(current)
            async with client.stream("GET", current) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise AppError("That link returned a bad redirect.")
                    current = str(response.url.join(location))
                    continue
                response.raise_for_status()
                content_type = (response.headers.get("content-type") or "").lower()
                if content_type and not any(
                    token in content_type for token in ("text/html", "text/plain", "application/xhtml")
                ):
                    raise AppError("That link did not return a readable article page.")
                # Stream with a hard cap so a large file never fully buffers.
                chunks: list[bytes] = []
                seen = 0
                async for piece in response.aiter_bytes(chunk_size=65536):
                    seen += len(piece)
                    if seen > 2_000_000:
                        raise AppError("That page is too large to ingest.")
                    chunks.append(piece)
                html = b"".join(chunks).decode(response.charset_encoding or "utf-8", errors="replace")
                final_url = str(response.url)
                assert_public_http_url(final_url)
                final_response = True
                break
        if not final_response:
            raise AppError("That link redirected too many times.")
    text = _extract_article(html, final_url)
    if len(text.strip()) < 40:
        raise AppError("Could not extract article text from that page.")
    return text


def _extract_article(html: str, url: str) -> str:
    try:
        import trafilatura

        extracted = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=True,
            favor_precision=True,
        )
        if extracted and extracted.strip():
            return extracted.strip()
    except Exception:
        pass
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    body = soup.get_text("\n", strip=True)
    return f"{title}\n\n{body}".strip()


def _youtube_id(url: str) -> str | None:
    match = YOUTUBE_RE.search(url)
    if match:
        return match.group(1)
    parsed = urlparse(url)
    if "youtube.com" in (parsed.hostname or ""):
        values = parse_qs(parsed.query).get("v") or []
        if values and re.fullmatch(r"[A-Za-z0-9_-]{11}", values[0]):
            return values[0]
    return None


def _youtube_transcript(url: str) -> str:
    video_id = _youtube_id(url)
    if not video_id:
        raise AppError("That does not look like a YouTube link.")
    from youtube_transcript_api import YouTubeTranscriptApi

    snippets = _load_transcript(YouTubeTranscriptApi, video_id)
    lines = []
    for snippet in snippets:
        if isinstance(snippet, dict):
            text = snippet.get("text", "")
        else:
            text = getattr(snippet, "text", "")
        text = " ".join(str(text).split())
        if text:
            lines.append(text)
    if not lines:
        raise AppError("No transcript is available for that video.")
    return "\n".join(lines)


def _load_transcript(api_cls: type, video_id: str) -> list:
    instance = api_cls()
    fetch = getattr(instance, "fetch", None)
    if callable(fetch):
        fetched = fetch(video_id)
        return list(fetched)
    legacy = getattr(api_cls, "get_transcript", None)
    if callable(legacy):
        return list(legacy(video_id))
    raise AppError("The transcript helper is unavailable.")


def _store_original(source_id: str, filename: str, file_bytes: bytes | None, text: str) -> None:
    folder = UPLOAD_DIR / source_id
    folder.mkdir(parents=True, exist_ok=True)
    if file_bytes:
        (folder / safe_filename(filename)).write_bytes(file_bytes)
    (folder / "extracted.txt").write_text(text, encoding="utf-8")
