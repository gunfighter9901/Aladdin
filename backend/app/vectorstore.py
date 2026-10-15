"""One Qdrant collection. Sources are isolated by sourceId, never by extra collections.

Retrieval always applies a Qdrant MatchAny filter, which is the $in operator:
queries search only the active source ids. An empty selection returns nothing
and does not hit the store. Muted sources are simply absent from that list.

Embeddings come from the standard FastEmbed package
(`BAAI/bge-small-en-v1.5`). The model weights live in the virtualenv's
FastEmbed cache (`$VIRTUAL_ENV/fastembed_cache`, via FASTEMBED_CACHE_PATH)
and are fetched once with `python scripts/download_model.py` after
`pip install -r requirements.txt`. Nothing is downloaded on server startup.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastembed import TextEmbedding
from qdrant_client import QdrantClient
from qdrant_client.models import (
    BinaryQuantization,
    BinaryQuantizationConfig,
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchAny,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from app.config import QDRANT_DIR, settings

from opik import track

COLLECTION = settings.collection_name
EMBEDDING_MODEL = settings.embedding_model
VECTOR_SIZE = 384


@dataclass(frozen=True)
class Hit:
    source_id: str
    filename: str
    chunk_id: str
    chunk_index: int
    page_number: int | None
    text: str
    score: float

    def citation(self) -> dict[str, Any]:
        return {
            "sourceId": self.source_id,
            "filename": self.filename,
            "chunkId": self.chunk_id,
            "pageNumber": self.page_number,
        }


def source_in_filter(source_ids: list[str]) -> Filter:
    """Qdrant $in filter. MatchAny is the engine's any/$in operator."""
    return Filter(
        must=[
            FieldCondition(
                key="sourceId",
                match=MatchAny(any=list(source_ids)),
            )
        ]
    )


_embedder: TextEmbedding | None = None
_embedder_lock = threading.Lock()


def get_embedder() -> TextEmbedding:
    """Shared FastEmbed model. Weights live in FASTEMBED_CACHE_PATH (virtualenv cache)."""
    global _embedder
    # Fast path without lock once initialized; creation is guarded.
    if _embedder is not None:
        return _embedder
    with _embedder_lock:
        if _embedder is None:
            _embedder = TextEmbedding(model_name=EMBEDDING_MODEL)
        return _embedder


def _embed_texts(texts: list[str]) -> list[list[float]]:
    # Do not hold the creation lock during inference. FastEmbed's embed()
    # generator would otherwise serialize all ingest/search calls and block
    # collection health checks behind a long embedding batch.
    embedder = get_embedder()
    return [list(vec) for vec in embedder.embed(texts)]


class VectorStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        QDRANT_DIR.mkdir(parents=True, exist_ok=True)
        self.client = QdrantClient(path=str(QDRANT_DIR))
        self.collection = COLLECTION

    def close(self) -> None:
        with self._lock:
            self.client.close()

    def ensure_collection(self) -> None:
        with self._lock:
            self._ensure_collection_locked()

    def _ensure_collection_locked(self) -> None:
        if not self.client.collection_exists(self.collection):
            quantization = BinaryQuantization(binary=BinaryQuantizationConfig(always_ram=True))
            try:
                self.client.create_collection(
                    collection_name=self.collection,
                    vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
                    quantization_config=quantization,
                    on_disk_payload=True,
                )
            except Exception:
                if self.client.collection_exists(self.collection):
                    raise
                self.client.create_collection(
                    collection_name=self.collection,
                    vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
                )
        try:
            self.client.create_payload_index(
                collection_name=self.collection,
                field_name="sourceId",
                field_schema=PayloadSchemaType.KEYWORD,
            )
        except Exception:
            # The index already exists after the first startup.
            # Local Qdrant also ignores payload indexes; filtering still works.
            return

    def upsert_chunks(
        self,
        *,
        source_id: str,
        filename: str,
        chunks: list[Any],
    ) -> int:
        return _upsert_span(self, source_id=source_id, filename=filename, chunks=chunks)

    def delete_source(self, source_id: str) -> None:
        with self._lock:
            if not self.client.collection_exists(self.collection):
                return
            self.client.delete(
                collection_name=self.collection,
                points_selector=FilterSelector(
                    filter=Filter(
                        must=[
                            FieldCondition(
                                key="sourceId",
                                match=MatchValue(value=source_id),
                            )
                        ]
                    )
                ),
            )

    def search(self, query: str, source_ids: list[str], limit: int | None = None) -> list[Hit]:
        """Empty selection retrieves nothing. The $in filter is mandatory."""
        return _search_span(self, query, list(source_ids or []), limit)

    def sample(self, source_ids: list[str], limit: int = 8) -> list[Hit]:
        return _sample_span(self, list(source_ids or []), limit)

    def count(self) -> int:
        with self._lock:
            if not self.client.collection_exists(self.collection):
                return 0
            result = self.client.count(collection_name=self.collection, exact=True)
        return int(getattr(result, "count", 0))


def _hit_from_point(point: Any) -> Hit | None:
    payload = getattr(point, "payload", None) or {}
    score = float(getattr(point, "score", 0.0) or 0.0)
    return _hit_from_payload(payload, score)


@track(name="qdrant_upsert", type="tool", ignore_arguments=["store", "chunks"])
def _upsert_span(store: VectorStore, *, source_id: str, filename: str, chunks: list[Any]) -> int:
    from app.observability import update_span

    if not chunks:
        update_span(output={"chunk_count": 0})
        return 0
    documents = [chunk.text for chunk in chunks]
    vectors = _embed_texts(documents)
    created = datetime.now(timezone.utc).isoformat()
    points = []
    for chunk, vector in zip(chunks, vectors):
        # chunkId must be globally unique: citations only carry
        # (filename, chunkId), so two different "notes.txt" files must
        # never produce the same chunkId. Use the full source uuid.
        chunk_id = f"{source_id}-c{chunk.chunk_index}"
        points.append(
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={
                    "sourceId": source_id,
                    "filename": filename,
                    "pageNumber": chunk.page_number,
                    "chunkIndex": chunk.chunk_index,
                    "chunkId": chunk_id,
                    "createdTime": created,
                    "text": chunk.text,
                },
            )
        )
    with store._lock:
        store._ensure_collection_locked()
        store.client.upsert(collection_name=store.collection, points=points)
    update_span(
        input={"source_id": source_id, "filename": filename, "chunk_count": len(chunks)},
        output={"upserted": len(chunks)},
    )
    return len(chunks)


@track(name="qdrant_search", type="tool", ignore_arguments=["store"])
def _search_span(store: VectorStore, query: str, source_ids: list[str], limit: int | None) -> list[Hit]:
    """Qdrant $in search. Logs query, $in ids, and chunk previews as a Span."""
    from app.observability import chunk_preview, truncate, update_span

    ids = [item for item in source_ids if item]
    if not ids or not query.strip():
        update_span(
            input={"query": truncate(query, 1000), "source_ids": ids},
            output={"hit_count": 0, "empty": True},
        )
        return []
    cap = limit or settings.retrieval_limit
    query_vector = _embed_texts([query.strip()])[0]
    with store._lock:
        if not store.client.collection_exists(store.collection):
            update_span(output={"hit_count": 0, "no_collection": True})
            return []
        response = store.client.query_points(
            collection_name=store.collection,
            query=query_vector,
            query_filter=source_in_filter(ids),
            limit=cap,
            with_payload=True,
        )
        points = list(response.points)
    hits = [_hit_from_point(point) for point in points]
    hits = [hit for hit in hits if hit is not None and hit.source_id in set(ids)]
    update_span(
        input={"query": truncate(query, 1000), "source_ids": ids, "limit": cap},
        output={"hit_count": len(hits)},
        metadata={"chunks": chunk_preview(hits)},
    )
    return hits


@track(name="qdrant_sample", type="tool", ignore_arguments=["store"])
def _sample_span(store: VectorStore, source_ids: list[str], limit: int) -> list[Hit]:
    from app.observability import chunk_preview, update_span

    ids = [item for item in source_ids if item]
    if not ids:
        update_span(output={"hit_count": 0, "empty": True})
        return []
    with store._lock:
        if not store.client.collection_exists(store.collection):
            update_span(output={"hit_count": 0, "no_collection": True})
            return []
        records, _ = store.client.scroll(
            collection_name=store.collection,
            scroll_filter=source_in_filter(ids),
            limit=limit,
            with_payload=True,
        )
    hits: list[Hit] = []
    allowed = set(ids)
    for record in records:
        hit = _hit_from_payload(getattr(record, "payload", None) or {}, score=0.0)
        if hit and hit.source_id in allowed:
            hits.append(hit)
    hits.sort(key=lambda item: (item.source_id, item.chunk_index))
    update_span(
        input={"source_ids": ids, "limit": limit},
        output={"hit_count": len(hits)},
        metadata={"chunks": chunk_preview(hits)},
    )
    return hits


def _hit_from_payload(payload: dict[str, Any], score: float) -> Hit | None:
    text = payload.get("text") or payload.get("document") or ""
    source_id = payload.get("sourceId")
    chunk_id = payload.get("chunkId")
    if not source_id or not chunk_id or not text:
        return None
    page = payload.get("pageNumber")
    return Hit(
        source_id=str(source_id),
        filename=str(payload.get("filename") or "source"),
        chunk_id=str(chunk_id),
        chunk_index=int(payload.get("chunkIndex") or 0),
        page_number=int(page) if isinstance(page, int) else None,
        text=str(text),
        score=score,
    )


_store: VectorStore | None = None
_store_lock = threading.Lock()


def get_store() -> VectorStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = VectorStore()
        return _store


def close_store() -> None:
    global _store
    with _store_lock:
        if _store is not None:
            _store.close()
            _store = None
