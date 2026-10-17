"""One Qdrant collection. Sources are isolated by sourceId, never by extra collections.

Retrieval always applies a Qdrant MatchAny filter, which is the $in operator:
queries search only the active source ids. An empty selection returns nothing
and does not hit the store. Muted sources are simply absent from that list.

Retrieval is hybrid: dense semantic search (FastEmbed cosine) + async BM25
keyword search, fused with RRF. The fused order is the final ranking and the
backend takes top-k chunks from it (``VectorStore.search``).

Embeddings come from the standard FastEmbed package
(`BAAI/bge-small-en-v1.5`). The model weights live in the virtualenv's
FastEmbed cache (`$VIRTUAL_ENV/fastembed_cache`, via FASTEMBED_CACHE_PATH)
and are fetched once with `python scripts/download_model.py` after
`pip install -r requirements.txt`. Nothing is downloaded on server startup.
"""

from __future__ import annotations

import asyncio
import logging
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

logger = logging.getLogger("educator.vectorstore")


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
        except Exception as exc:
            # The index already exists after the first startup.
            # Local Qdrant also ignores payload indexes; filtering still works.
            logger.debug("Payload index creation skipped: %s", type(exc).__name__)
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
        """Hybrid search (dense + BM25 + RRF). This fused order is final; top-k returned."""
        return _hybrid_span(self, query, list(source_ids or []), limit)

    def dense_search(self, query: str, source_ids: list[str], limit: int | None = None) -> list[Hit]:
        """Dense semantic search only (FastEmbed cosine). Used for ablation/debug."""
        return _search_span(self, query, list(source_ids or []), limit)

    def bm25_search(self, query: str, source_ids: list[str], limit: int | None = None) -> list[Hit]:
        """BM25 keyword search only (no embeddings). Sync core for the async wrapper."""
        return _bm25_span(self, query, list(source_ids or []), limit)

    def hybrid_search(self, query: str, source_ids: list[str], limit: int | None = None) -> list[Hit]:
        """Hybrid search (dense + BM25 + RRF). Same as search(); top-k from fused rank."""
        return _hybrid_span(self, query, list(source_ids or []), limit)

    async def abm25_search(
        self, query: str, source_ids: list[str], limit: int | None = None
    ) -> list[Hit]:
        """Async BM25 keyword search. Scroll + scoring run off the event loop."""
        return await asyncio.to_thread(self.bm25_search, query, list(source_ids or []), limit)

    async def ahybrid_search(
        self, query: str, source_ids: list[str], limit: int | None = None
    ) -> list[Hit]:
        """Async hybrid search. Dense + BM25 run concurrently, then RRF fuse to top-k."""
        return await _ahybrid_span(self, query, list(source_ids or []), limit)

    def sample(self, source_ids: list[str], limit: int = 8) -> list[Hit]:
        return _sample_span(self, list(source_ids or []), max(1, min(int(limit or 8), 64)))

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
    if vectors and len(vectors[0]) != VECTOR_SIZE:
        logger.warning(
            "Embedding dimension %s does not match collection size %s; recreate the collection",
            len(vectors[0]),
            VECTOR_SIZE,
        )
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
    cap = max(1, min(int(cap), 20))
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


def _scroll_candidates(store: VectorStore, ids: list[str], cap: int) -> list[Hit]:
    """Scroll up to `cap` $in-filtered points for BM25 scoring. Never raises."""
    allowed = set(ids)
    out: list[Hit] = []
    offset: Any = None
    page = 256
    try:
        with store._lock:
            if not store.client.collection_exists(store.collection):
                return []
        while len(out) < cap:
            fetch = min(page, cap - len(out))
            with store._lock:
                records, offset = store.client.scroll(
                    collection_name=store.collection,
                    scroll_filter=source_in_filter(ids),
                    limit=fetch,
                    offset=offset,
                    with_payload=True,
                )
            if not records:
                break
            for record in records:
                hit = _hit_from_payload(getattr(record, "payload", None) or {}, score=0.0)
                if hit and hit.source_id in allowed:
                    out.append(hit)
                    if len(out) >= cap:
                        break
            if offset is None:
                break
    except Exception as exc:
        logger.debug("BM25 candidate scroll skipped: %s", type(exc).__name__)
    return out


@track(name="bm25_search", type="tool", ignore_arguments=["store"])
def _bm25_span(store: VectorStore, query: str, source_ids: list[str], limit: int | None) -> list[Hit]:
    """Async-friendly BM25 keyword search over $in-filtered chunks. No embeddings."""
    from app.bm25 import BM25, tokenize
    from app.observability import chunk_preview, truncate, update_span

    ids = [item for item in source_ids if item]
    if not ids or not query.strip():
        update_span(
            input={"query": truncate(query, 1000), "source_ids": ids},
            output={"hit_count": 0, "empty": True},
        )
        return []
    query_tokens = tokenize(query)
    if not query_tokens:
        update_span(
            input={"query": truncate(query, 1000), "source_ids": ids},
            output={"hit_count": 0, "empty_query_tokens": True},
        )
        return []
    cap = limit or settings.retrieval_limit
    cap = max(1, min(int(cap), 20))
    candidate_cap = max(1, int(settings.bm25_candidate_cap))
    candidates = _scroll_candidates(store, ids, candidate_cap)
    if not candidates:
        update_span(
            input={"query": truncate(query, 1000), "source_ids": ids},
            output={"hit_count": 0, "no_candidates": True},
        )
        return []
    docs_tokens = [tokenize(hit.text) for hit in candidates]
    scorer = BM25(docs_tokens, k1=settings.bm25_k1, b=settings.bm25_b)
    scores = scorer.score(query_tokens)
    ranked = sorted(zip(candidates, scores), key=lambda pair: pair[1], reverse=True)
    hits: list[Hit] = []
    for hit, score in ranked[:cap]:
        if score <= 0:
            continue
        hits.append(
            Hit(
                source_id=hit.source_id,
                filename=hit.filename,
                chunk_id=hit.chunk_id,
                chunk_index=hit.chunk_index,
                page_number=hit.page_number,
                text=hit.text,
                score=float(score),
            )
        )
    update_span(
        input={"query": truncate(query, 1000), "source_ids": ids, "limit": cap},
        output={"hit_count": len(hits), "candidates": len(candidates)},
        metadata={"chunks": chunk_preview(hits)},
    )
    return hits


def _fuse_hits(dense_hits: list[Hit], bm25_hits: list[Hit], limit: int) -> list[Hit]:
    """RRF fuse two ranked lists. Fused order is final; caller takes top-k."""
    from app.bm25 import rrf_fuse

    dense_rank = {(h.source_id, h.chunk_id): i + 1 for i, h in enumerate(dense_hits or [])}
    bm25_rank = {(h.source_id, h.chunk_id): i + 1 for i, h in enumerate(bm25_hits or [])}
    fused_scores = rrf_fuse(
        dense_rank,
        bm25_rank,
        k=max(1, int(settings.hybrid_rrf_k)),
        dense_weight=float(settings.hybrid_dense_weight),
        bm25_weight=float(settings.hybrid_bm25_weight),
    )
    if not fused_scores:
        return []
    by_key: dict[tuple[str, str], Hit] = {}
    for hit in list(dense_hits or []) + list(bm25_hits or []):
        by_key.setdefault((hit.source_id, hit.chunk_id), hit)
    fused: list[Hit] = []
    for key, fused_score in fused_scores.items():
        base = by_key.get(key)
        if base is None:
            continue
        fused.append(
            Hit(
                source_id=base.source_id,
                filename=base.filename,
                chunk_id=base.chunk_id,
                chunk_index=base.chunk_index,
                page_number=base.page_number,
                text=base.text,
                score=float(fused_score),
            )
        )
    fused.sort(key=lambda h: h.score, reverse=True)
    return fused[: max(1, limit)]


@track(name="hybrid_search", type="tool", ignore_arguments=["store"])
def _hybrid_span(store: VectorStore, query: str, source_ids: list[str], limit: int | None) -> list[Hit]:
    """Hybrid search: dense + BM25 run, RRF rerank, top-k returned as final ranking."""
    from app.observability import chunk_preview, truncate, update_span

    ids = [item for item in source_ids if item]
    if not ids or not query.strip():
        update_span(
            input={"query": truncate(query, 1000), "source_ids": ids},
            output={"hit_count": 0, "empty": True},
        )
        return []
    cap = limit or settings.retrieval_limit
    cap = max(1, min(int(cap), 20))
    branch = max(cap, min(int(settings.hybrid_overfetch), 20))
    try:
        dense_hits = _search_span(store, query, ids, branch)
    except Exception as exc:
        logger.debug("Hybrid dense branch skipped: %s", type(exc).__name__)
        dense_hits = []
    try:
        bm25_hits = _bm25_span(store, query, ids, branch)
    except Exception as exc:
        logger.debug("Hybrid BM25 branch skipped: %s", type(exc).__name__)
        bm25_hits = []
    hits = _fuse_hits(dense_hits, bm25_hits, cap)
    # Post-filter: muted sources must never leak through fusion.
    allowed = set(ids)
    hits = [h for h in hits if h.source_id in allowed]
    update_span(
        input={"query": truncate(query, 1000), "source_ids": ids, "limit": cap},
        output={
            "hit_count": len(hits),
            "dense_hit_count": len(dense_hits),
            "bm25_hit_count": len(bm25_hits),
        },
        metadata={"chunks": chunk_preview(hits)},
    )
    return hits


async def _ahybrid_span(store: VectorStore, query: str, source_ids: list[str], limit: int | None) -> list[Hit]:
    """Async hybrid: dense + BM25 concurrently off the event loop, RRF fuse to top-k."""
    from app.observability import chunk_preview, truncate, update_span

    ids = [item for item in source_ids if item]
    if not ids or not (query or "").strip():
        return []
    cap = limit or settings.retrieval_limit
    cap = max(1, min(int(cap), 20))
    branch = max(cap, min(int(settings.hybrid_overfetch), 20))
    dense_task = asyncio.to_thread(_search_span, store, query, ids, branch)
    bm25_task = asyncio.to_thread(_bm25_span, store, query, ids, branch)
    results = await asyncio.gather(dense_task, bm25_task, return_exceptions=True)
    dense_hits = results[0] if isinstance(results[0], list) else []
    bm25_hits = results[1] if isinstance(results[1], list) else []
    hits = _fuse_hits(dense_hits, bm25_hits, cap)
    allowed = set(ids)
    hits = [h for h in hits if h.source_id in allowed]
    update_span(
        input={"query": truncate(query, 1000), "source_ids": ids, "limit": cap},
        output={
            "hit_count": len(hits),
            "dense_hit_count": len(dense_hits),
            "bm25_hit_count": len(bm25_hits),
        },
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
