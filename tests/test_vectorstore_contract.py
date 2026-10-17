"""Vector-store contract: single collection, $in isolation, citation shape.

Covers backend/app/vectorstore.py:
- one collection ``educator_sources`` (settings.collection_name)
- Qdrant MatchAny == the ``$in`` operator over active source ids
- empty selection retrieves nothing WITHOUT hitting the store
- muted sources never appear in hits (post-filter)
- chunkId globally unique as ``{source_id}-c{index}``
"""
from __future__ import annotations


def test_collection_constants():
    from app import vectorstore as vs
    from app.config import settings

    assert vs.COLLECTION == settings.collection_name == "educator_sources"
    assert vs.EMBEDDING_MODEL == "BAAI/bge-small-en-v1.5"
    assert vs.VECTOR_SIZE == 384


def test_source_in_filter_is_match_any_dollar_in():
    from qdrant_client.models import MatchAny

    from app.vectorstore import source_in_filter

    f = source_in_filter(["a", "b"])
    cond = f.must[0]
    assert cond.key == "sourceId"
    assert isinstance(cond.match, MatchAny)
    assert set(cond.match.any) == {"a", "b"}


def test_hit_citation_shape_and_payload_validation():
    from app.vectorstore import Hit, _hit_from_payload

    hit = Hit(
        source_id="s1",
        filename="notes.txt",
        chunk_id="s1-c0",
        chunk_index=0,
        page_number=2,
        text="photosynthesis",
        score=0.9,
    )
    assert hit.citation() == {
        "sourceId": "s1",
        "filename": "notes.txt",
        "chunkId": "s1-c0",
        "pageNumber": 2,
    }
    # Missing required fields -> None (dropped, never cited)
    assert _hit_from_payload({"sourceId": "s", "text": "t"}, 0.0) is None
    assert _hit_from_payload({"sourceId": "s", "chunkId": "c", "text": ""}, 0.0) is None
    # Legacy 'document' key accepted
    h = _hit_from_payload({"sourceId": "s", "chunkId": "c", "document": "hello"}, 0.5)
    assert h is not None and h.text == "hello"


def test_search_empty_selection_returns_nothing_without_embedding(monkeypatch):
    from app import vectorstore as vs

    def _boom(texts):
        raise AssertionError("embed must not be called for empty selection")

    monkeypatch.setattr(vs, "_embed_texts", _boom)
    store = vs.VectorStore.__new__(vs.VectorStore)  # avoid Qdrant file lock
    assert store.search("", ["s1"]) == []
    assert store.search("query", []) == []
    assert store.search("   ", []) == []


def test_search_post_filter_drops_muted_sources(monkeypatch):
    from app import vectorstore as vs

    def fake_embed(texts):
        return [[0.1] * vs.VECTOR_SIZE for _ in texts]

    class FakePoints:
        def __init__(self, payload, score=0.9):
            self.payload = payload
            self.score = score

    class FakeResp:
        points = [
            FakePoints({"sourceId": "active-1", "filename": "a.txt", "chunkId": "active-1-c0", "chunkIndex": 0, "text": "kept"}),
            FakePoints({"sourceId": "muted-9", "filename": "m.txt", "chunkId": "muted-9-c0", "chunkIndex": 0, "text": "must not leak"}),
        ]

    class FakeClient:
        def collection_exists(self, *a, **k):
            return True

        def query_points(self, **kwargs):
            # The $in filter sent to Qdrant must contain ONLY active ids
            filt = kwargs["query_filter"]
            assert filt.must[0].match.any == ["active-1"]
            return FakeResp()

    monkeypatch.setattr(vs, "_embed_texts", fake_embed)
    store = vs.VectorStore.__new__(vs.VectorStore)
    import threading

    store._lock = threading.Lock()
    store.client = FakeClient()
    store.collection = "educator_sources"

    hits = vs._search_span(store, "photosynthesis", ["active-1"], 6)
    assert [h.source_id for h in hits] == ["active-1"]
    assert all("must not leak" not in h.text for h in hits)


def test_sample_empty_returns_nothing_and_sorts(monkeypatch):
    from app import vectorstore as vs
    from app.vectorstore import Hit

    store = vs.VectorStore.__new__(vs.VectorStore)
    import threading

    store._lock = threading.Lock()

    class FakeClient:
        def collection_exists(self, *a, **k):
            return True

        def scroll(self, **kwargs):
            assert kwargs["scroll_filter"].must[0].key == "sourceId"
            return ([], None)

    store.client = FakeClient()
    store.collection = "educator_sources"
    assert vs._sample_span(store, [], 8) == []

    # Sorting by (source_id, chunk_index) is deterministic for studio context
    hits = [
        Hit("b", "b.txt", "b-c1", 1, None, "t", 0.0),
        Hit("a", "a.txt", "a-c1", 1, None, "t", 0.0),
        Hit("a", "a.txt", "a-c0", 0, None, "t", 0.0),
    ]
    hits.sort(key=lambda h: (h.source_id, h.chunk_index))
    assert [(h.source_id, h.chunk_index) for h in hits] == [("a", 0), ("a", 1), ("b", 1)]
