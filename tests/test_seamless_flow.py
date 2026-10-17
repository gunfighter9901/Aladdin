"""Seamless flow: ingest -> scope -> tutor context -> studio -> session memory.

This is the project's proof-of-working: one story across every layer.
No network, no Gemini, no Qdrant binary: the vector store and the LLM
are faked, but the contracts (isolation, citations, artifacts) are real.
"""
from __future__ import annotations


def _mock_text_pipeline(monkeypatch, summary="Study summary"):
    from app import chunking as ch_mod
    from app import generators as gen_mod

    class FakeChunk:
        def __init__(self, i):
            self.text = f"passage {i} about the carbon cycle"
            self.chunk_index = i
            self.page_number = None

    class FakeChunker:
        def chunk_document(self, pages):
            joined = "\n\n".join(t for _, t in pages)
            n = max(1, min(3, len(joined.split()) // 3 + 1))
            return [FakeChunk(i) for i in range(n)]

    async def fake_summary(filename, text):
        return summary

    monkeypatch.setattr(ch_mod, "get_chunker", lambda: FakeChunker())
    monkeypatch.setattr(gen_mod, "summarize_source", fake_summary)


def test_full_journey_ingest_scope_study_artifacts(client, monkeypatch):
    from app.vectorstore import Hit

    _mock_text_pipeline(monkeypatch)

    # 1. Ingest TWO sources (mimics PDFs/notes/URLs -> chunks -> vectors)
    r1 = client.post("/api/upload-source", data={"text": "Carbon cycle part one.", "title": "Carbon A"})
    r2 = client.post("/api/upload-source", data={"text": "Nitrogen cycle part one.", "title": "Nitrogen B"})
    assert r1.status_code == 201 and r2.status_code == 201
    s1, s2 = r1.json()["id"], r2.json()["id"]

    # 2. Student checks ONLY Carbon A (left panel scope = $in list)
    assert client.patch(f"/api/sources/{s2}", json={"active": False}).status_code == 200
    sources = {s["id"]: s for s in client.get("/api/sources").json()}
    assert sources[s1]["active"] is True and sources[s2]["active"] is False
    active_ids = [s["id"] for s in sources.values() if s["active"] and s["status"] == "ready"]
    assert active_ids == [s1]

    # 3. Retrieval isolation: muted source can never leak into tutor context
    client.fake_store.hits = [
        Hit(s1, sources[s1]["filename"], f"{s1}-c0", 0, None, "carbon passage", 0.9),
        Hit(s2, sources[s2]["filename"], f"{s2}-c0", 0, None, "nitrogen passage", 0.9),
    ]
    from app.generators import _study_context
    import asyncio

    ctx, hits = asyncio.new_event_loop().run_until_complete(_study_context(active_ids, "carbon"))
    assert "carbon passage" in ctx
    assert "nitrogen passage" not in ctx
    assert {h.source_id for h in hits} == {s1}

    # 4. Tutor grounding: hallucinated citations are dropped, real ones kept
    from app.chat_runner import _citations

    answer = (
        f"Carbon moves through air [Source: {sources[s1]['filename']}, Chunk: {s1}-c0] "
        f"and nitrogen does too [Source: {sources[s2]['filename']}, Chunk: {s2}-c0]."
    )
    kept = _citations(answer, hits)
    assert [(c["sourceId"], c["chunkId"]) for c in kept] == [(s1, f"{s1}-c0")]

    # 5. Studio generation from the SAME scope (right panel)
    from app import generators as g

    class FakeGemini:
        configured = True

        async def generate_json(self, prompt, schema, temperature=0.3):
            if "flashcards" in prompt.lower() or "multiple-choice" in prompt.lower():
                return {
                    "title": "Carbon quiz",
                    "questions": [{"id": "q1", "question": "What moves?", "options": ["C", "N", "O", "H"], "correctAnswer": "C", "rationale": "In the passage."}],
                    "flashcards": [{"id": "f1", "front": "Gas?", "back": "Carbon"}],
                }
            return {"title": "Carbon cast", "turns": [{"speaker": "Joe", "text": "Hi."}, {"speaker": "Jane", "text": "Carbon cycles."}]}

        async def generate_text(self, prompt, temperature=0.3):
            return "mindmap\n  root((carbon))"

        async def synthesize_podcast(self, dialogue):
            return ("", "audio/wav")

    monkeypatch.setattr(g, "get_gemini", lambda: FakeGemini())

    sess = client.post("/api/sessions", json={"title": "Cycle study"}).json()
    sid = sess["id"]

    qz = client.post("/api/generate-quiz", json={"sourceIds": active_ids, "sessionId": sid, "focus": "carbon", "count": 1})
    assert qz.status_code == 200, qz.text
    assert qz.json()["questions"][0]["correctAnswer"] == "C"

    dz = client.post("/api/generate-infographic", json={"sourceIds": active_ids, "sessionId": sid, "diagramType": "mindmap"})
    assert dz.status_code == 200
    assert dz.json()["mermaid"].startswith("mindmap")

    # Empty scope retrieves nothing (guardrail, not silent failure)
    bad = client.post("/api/generate-quiz", json={"sourceIds": [], "focus": "x"})
    assert bad.status_code == 400

    # 6. Session memory: messages + studio artifacts persist together
    detail = client.get(f"/api/sessions/{sid}").json()
    kinds = {a["kind"] for a in detail["artifacts"]}
    assert {"quiz", "infographic"} <= kinds
    assert detail["session"]["id"] == sid


def test_empty_selection_chat_contract():
    """Documents the empty-scope rule the UI shows as 'empty retrieves nothing'."""
    from app.vectorstore import Hit  # noqa: F401 - contract import
    from app.chat_runner import _agent_message
    from app.generators import _study_context
    import asyncio

    from app.errors import AppError

    msg = _agent_message("Summarize?", [])
    assert "Do not invent citations" in msg
    try:
        asyncio.new_event_loop().run_until_complete(_study_context([], "anything"))
        raise AssertionError("empty selection must raise")
    except AppError as e:
        assert "empty" in e.message.lower() or "Select at least one" in e.message
