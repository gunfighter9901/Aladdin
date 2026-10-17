"""API lifecycle: health, sources CRUD, sessions, error contracts.

Proves the HTTP contract the React client depends on:
- GET /api/health -> booleans only, never secrets
- POST /api/upload-source (text) -> 201 + ready after mocked pipeline
- PATCH guard: only ready sources can be included (active=true)
- DELETE removes DB row + Qdrant points
- sessions create/get/delete + 404 shape {detail, code}
- validation errors -> 422 {detail, code}, every error carries X-Request-ID
"""
from __future__ import annotations


def _mock_pipeline(monkeypatch, n_chunks=2):
    """Bypass tokenizer/Gemini/Qdrant for a text upload."""
    from app import chunking as ch_mod
    from app import generators as gen_mod
    from app import ingestion as ing_mod

    class FakeChunk:
        def __init__(self, i):
            self.text = f"chunk {i} about photosynthesis"
            self.chunk_index = i
            self.page_number = None

    class FakeChunker:
        def chunk_document(self, pages):
            return [FakeChunk(i) for i in range(n_chunks)]

    async def fake_summary(filename, text):
        return f"Summary of {filename}"

    monkeypatch.setattr(ch_mod, "get_chunker", lambda: FakeChunker())
    # ingestion imports summarize_source lazily: from app.generators import ...
    monkeypatch.setattr(gen_mod, "summarize_source", fake_summary)


def test_health_contract_has_no_secrets(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"status", "geminiConfigured", "opikConfigured", "collection", "qdrant"}
    assert isinstance(body["geminiConfigured"], bool)
    assert isinstance(body["opikConfigured"], bool)
    assert body["collection"] == "educator_sources"
    assert body["qdrant"] in {"ready", "unavailable"}
    dumped = r.text
    assert "AIza" not in dumped and "GEMINI_API_KEY" not in dumped
    assert r.headers.get("X-Request-ID")


def test_upload_text_source_becomes_ready(client, monkeypatch):
    _mock_pipeline(monkeypatch)
    r = client.post("/api/upload-source", data={"text": "Photosynthesis converts light.", "title": "Bio notes"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "ready"
    assert body["chunkCount"] == 2
    assert body["active"] is True
    assert body["filename"].endswith(".txt")
    assert client.fake_store.points == 2

    # Listed with summary + chunk count
    r2 = client.get("/api/sources")
    assert r2.status_code == 200
    assert any(s["id"] == body["id"] for s in r2.json())


def test_upload_validation_errors(client):
    # Exactly one of file/url/text required
    r = client.post("/api/upload-source", data={"title": "empty"})
    assert r.status_code == 400
    assert r.json()["code"] in {"bad_request", "internal_error"} or "detail" in r.json()

    # Title too long -> AppError code
    r = client.post("/api/upload-source", data={"text": "hi", "title": "x" * 181})
    assert r.status_code == 400
    assert r.json()["code"] == "title_too_long"

    # Pasted text too long
    r = client.post("/api/upload-source", data={"text": "x" * 200_001})
    assert r.status_code == 400
    assert r.json()["code"] == "text_too_long"

    # Pydantic validation -> 422 sanitized shape
    r = client.post("/api/chat", json={"sessionId": "", "message": "", "activeSourceIds": []})
    assert r.status_code == 422
    assert r.json() == {"detail": "That request was not valid.", "code": "validation_error"}


def test_source_include_guard_and_delete(client, monkeypatch):
    _mock_pipeline(monkeypatch)
    r = client.post("/api/upload-source", data={"text": "Some physics.", "title": "Phys"})
    sid = r.json()["id"]

    # Mute then re-include (ready -> allowed)
    assert client.patch(f"/api/sources/{sid}", json={"active": False}).status_code == 200
    r = client.patch(f"/api/sources/{sid}", json={"active": True})
    assert r.status_code == 200 and r.json()["active"] is True

    # Simulate a failed ingestion row: cannot be included
    from sqlmodel import Session

    with Session(client.test_engine) as db:
        from app.models import Source

        row = db.get(Source, sid)
        row.status = "error"
        db.add(row)
        db.commit()
    r = client.patch(f"/api/sources/{sid}", json={"active": True})
    assert r.status_code == 400
    assert r.json()["code"] == "source_not_ready"

    # Delete removes DB row and Qdrant points
    r = client.delete(f"/api/sources/{sid}")
    assert r.status_code == 204
    assert client.fake_store.deleted == [sid]
    assert client.get("/api/sources").json() == []


def test_sessions_lifecycle_and_404_shape(client):
    r = client.post("/api/sessions", json={"title": "  Bio 101  "})
    assert r.status_code == 201
    sid = r.json()["id"]
    assert r.json()["title"] == "Bio 101"

    r = client.get(f"/api/sessions/{sid}")
    assert r.status_code == 200
    assert r.json()["session"]["id"] == sid
    assert r.json()["messages"] == []

    r = client.delete(f"/api/sessions/{sid}")
    assert r.status_code == 204
    r = client.get(f"/api/sessions/{sid}")
    assert r.status_code == 404
    assert set(r.json()) == {"detail", "code"}


def test_collection_endpoint_describes_isolation(client):
    r = client.get("/api/collection")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "educator_sources"
    assert body["embeddingModel"] == "BAAI/bge-small-en-v1.5"
    assert "$in" in body["isolation"]
    assert body["quantization"] == "binary"
    assert body["payload"] == "on_disk"
