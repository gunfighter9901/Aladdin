"""Source ingestion, selection, and the single vector collection."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from sqlmodel import Session, select

from app.config import settings
from app.database import get_session
from app.errors import AppError
from app.ingestion import delete_source_vectors, ingest_upload
from app.models import Source
from app.present import source_out
from app.schemas import CollectionOut, SourceOut, SourceUpdate
from app.vectorstore import get_store

router = APIRouter(prefix="/api", tags=["sources"])


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
        file_bytes = await file.read()
        if not file_bytes:
            raise AppError("That file is empty.")
        if len(file_bytes) > settings.max_upload_bytes:
            raise AppError("That file is larger than 25 MB.")
        file_name = file.filename
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
def list_sources(db: Session = Depends(get_session)) -> list[SourceOut]:
    rows = db.exec(select(Source).order_by(Source.created_at.desc())).all()
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
            raise AppError("Only a ready source can be included in answers.")
        source.active = body.active
    if body.title is not None and body.title.strip():
        source.title = body.title.strip()[:180]
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
