"""Source ingestion, selection, and the single vector collection."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from sqlmodel import Session, select

from app.config import settings
from app.database import get_session
from app.errors import AppError
from app.ingestion import delete_source_vectors, ingest_upload
from app.models import Source, utcnow
from app.present import source_out
from app.schemas import CollectionOut, SourceOut, SourceUpdate
from app.vectorstore import get_store

router = APIRouter(prefix="/api", tags=["sources"])


async def _read_upload_bytes(file: UploadFile) -> bytes:
    """Read an upload in chunks so the size cap applies before buffering all of it."""
    chunks: list[bytes] = []
    seen = 0
    while True:
        piece = await file.read(1024 * 1024)
        if not piece:
            break
        seen += len(piece)
        if seen > settings.max_upload_bytes:
            raise AppError(
                f"That file is larger than {settings.max_upload_bytes // (1024 * 1024)} MB.",
                code="file_too_large",
            )
        chunks.append(piece)
    data = b"".join(chunks)
    if not data:
        raise AppError("That file is empty.", code="file_empty")
    return data


@router.post("/upload-source", response_model=SourceOut, status_code=201)
async def upload_source(
    file: UploadFile | None = File(default=None),
    url: str | None = Form(default=None),
    text: str | None = Form(default=None),
    title: str | None = Form(default=None),
    db: Session = Depends(get_session),
) -> SourceOut:
    file_bytes = None
    file_name = None
    if file is not None and file.filename:
        file_bytes = await _read_upload_bytes(file)
        file_name = file.filename
    if title is not None and len(title) > 180:
        raise AppError("Title must be 180 characters or fewer.", code="title_too_long")
    if text is not None and len(text) > 200_000:
        raise AppError("Pasted text is too long. Split it into smaller notes.", code="text_too_long")
    source = await ingest_upload(
        db,
        file_name=file_name,
        file_bytes=file_bytes,
        url=url,
        text=text,
        title=title,
    )
    return source_out(source)


@router.get("/sources", response_model=list[SourceOut])
def list_sources(
    db: Session = Depends(get_session),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[SourceOut]:
    rows = db.exec(
        select(Source).order_by(Source.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return [source_out(row) for row in rows]


@router.patch("/sources/{source_id}", response_model=SourceOut)
def update_source(
    source_id: str,
    body: SourceUpdate,
    db: Session = Depends(get_session),
) -> SourceOut:
    source = db.get(Source, source_id)
    if source is None:
        raise AppError("That source was not found.", status_code=404)
    if body.active is not None:
        if body.active and source.status != "ready":
            raise AppError("Only a ready source can be included in answers.", code="source_not_ready")
        source.active = body.active
    if body.title is not None and body.title.strip():
        source.title = body.title.strip()[:180]
    source.updated_at = utcnow()
    db.add(source)
    db.commit()
    db.refresh(source)
    return source_out(source)


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(source_id: str, db: Session = Depends(get_session)) -> Response:
    import asyncio

    source = db.get(Source, source_id)
    if source is None:
        raise AppError("That source was not found.", status_code=404)
    await asyncio.to_thread(delete_source_vectors, source_id)
    db.delete(source)
    db.commit()
    return Response(status_code=204)


@router.get("/collection", response_model=CollectionOut)
async def collection_status() -> CollectionOut:
    import asyncio

    store = get_store()
    await asyncio.to_thread(store.ensure_collection)
    points = await asyncio.to_thread(store.count)
    return CollectionOut(
        name=settings.collection_name,
        points=points,
        embeddingModel=settings.embedding_model,
        isolation="one collection; each source is isolated by sourceId; retrieval uses a $in list of active ids",
        quantization="binary",
        payload="on_disk",
    )
