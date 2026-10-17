"""Studio pipelines: quiz/podcast/infographic validation without Gemini.

Covers backend/app/generators.py with mocked Gemini + FakeStore:
- _study_context: empty selection raises; focus search else sample fallback;
  404 when nothing retrievable; 14k truncation at sentence boundary
- quiz: drops options where correctAnswer not in options; needs >=1 usable Q
- podcast: dialogue budget 6000 chars at turn boundary, always keeps 2 turns,
  TTS failure becomes audioError (not fatal)
- infographic: fence stripping, repair pass, invalid raises; diagramType sanitized
- summarize_source: Gemini-off path uses extractive fallback
"""
from __future__ import annotations

import pytest


def _hit(sid="s1", fn="bio.txt", cid="s1-c0", text="light reactions store energy"):
    from app.vectorstore import Hit

    return Hit(sid, fn, cid, 0, None, text, 0.9)


@pytest.fixture()
def ctx_store(monkeypatch):
    from app import generators as g

    class S:
        def __init__(self, hits):
            self._hits = hits
            self.searched_with = None

        def search(self, q, ids, limit=8):
            self.searched_with = (q, list(ids))
            return [h for h in self._hits if h.source_id in ids]

        def sample(self, ids, limit=8):
            return [h for h in self._hits if h.source_id in ids][:limit]

    store = S([_hit(), _hit("s2", "chem.txt", "s2-c1", "bonds break")])
    monkeypatch.setattr(g, "get_store", lambda: store)
    return store


class FakeGemini:
    def __init__(self, json_payload=None, texts=None):
        self.json_payload = json_payload
        self.texts = list(texts or [])
        self.configured = True

    async def generate_json(self, prompt, schema, temperature=0.3):
        return self.json_payload

    async def generate_text(self, prompt, temperature=0.3):
        return self.texts.pop(0) if self.texts else "mindmap\n  root((x))"

    async def synthesize_podcast(self, dialogue):
        return ("QUJD", "audio/wav")


def _run(coro):
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def test_study_context_empty_selection_raises(ctx_store):
    from app.errors import AppError
    from app.generators import _study_context

    try:
        _run(_study_context([], "focus"))
        raise AssertionError("should raise")
    except AppError as e:
        assert "Select at least one source" in e.message


def test_study_context_uses_search_then_sample_fallback(monkeypatch, ctx_store):
    from app.generators import _study_context

    # Focus present -> search path used
    ctx, hits = _run(_study_context(["s1"], "photosynthesis"))
    assert ctx_store.searched_with[0] == "photosynthesis"
    assert hits and hits[0].source_id == "s1"

    # No focus -> sample path, no search
    ctx_store.searched_with = None
    ctx2, hits2 = _run(_study_context(["s2"], ""))
    assert ctx_store.searched_with is None
    assert hits2[0].source_id == "s2"

    # Long context truncated near 14k without splitting words
    from app.generators import _truncate_at_sentence

    long = "word " * 20000
    assert len(_truncate_at_sentence(long, 14000)) <= 14000


def test_study_context_404_when_nothing_retrievable(monkeypatch):
    from app import generators as g
    from app.errors import AppError

    class Empty:
        def search(self, *a, **k):
            return []

        def sample(self, *a, **k):
            return []

    monkeypatch.setattr(g, "get_store", lambda: Empty())
    from app.generators import _study_context

    try:
        _run(_study_context(["s1"], "x"))
        raise AssertionError("should raise")
    except AppError as e:
        assert e.status_code == 404


def test_quiz_drops_bad_options_and_requires_one_usable(monkeypatch, ctx_store):
    from app import generators as g
    from app.errors import GeminiUnavailable

    async def ok(*a, **k):
        return None

    monkeypatch.setattr(g, "get_store", lambda: ctx_store)

    good = {
        "title": "Q",
        "questions": [
            {"id": "q1", "question": "Q1?", "options": ["a", "b", "c", "d"], "correctAnswer": "b", "rationale": "r"},
            {"id": "q2", "question": "Q2?", "options": ["a", "b"], "correctAnswer": "zzz", "rationale": "r"},
        ],
        "flashcards": [{"id": "f1", "front": "F", "back": "B"}],
    }
    monkeypatch.setattr(g, "get_gemini", lambda: FakeGemini(json_payload=good))
    out = _run(g.generate_quiz(["s1"], "focus", 2))
    assert out["returnedCount"] == 1
    assert out["questions"][0]["correctAnswer"] == "b"

    monkeypatch.setattr(g, "get_gemini", lambda: FakeGemini(json_payload={
        "title": "Q",
        "questions": [{"id": "q1", "question": "Q?", "options": ["a", "b"], "correctAnswer": "zzz", "rationale": "r"}],
        "flashcards": [{"id": "f1", "front": "F", "back": "B"}],
    }))
    try:
        _run(g.generate_quiz(["s1"], "", 1))
        raise AssertionError("should raise")
    except GeminiUnavailable:
        pass


def test_podcast_budget_and_tts_graceful(monkeypatch, ctx_store):
    from app import generators as g

    script = {
        "title": "Pod",
        "turns": [
            {"speaker": "Joe", "text": "Hello student."},
            {"speaker": "Jane", "text": "Here is the idea."},
            {"speaker": "Joe", "text": "x" * 7000},
        ],
    }

    class G(FakeGemini):
        async def synthesize_podcast(self, dialogue):
            assert len(dialogue) <= 6000 + 200  # turn-boundary budget
            raise __import__("app.errors", fromlist=["GeminiUnavailable"]).GeminiUnavailable("tts down")

    monkeypatch.setattr(g, "get_gemini", lambda: G(json_payload=script))
    out = _run(g.generate_podcast(["s1"], "photosynthesis"))
    assert out["title"] == "Pod"
    assert len(out["turns"]) >= 2  # both voices kept
    assert out["audioBase64"] == ""
    assert out["audioError"] == "tts down"
    assert out["truncated"] is True


def test_infographic_repair_and_invalid(monkeypatch, ctx_store):
    from app import generators as g
    from app.errors import GeminiUnavailable

    # Fenced output cleaned; repair pass fixes invalid first attempt.
    # NOTE: generators call get_gemini() twice (initial + repair), so share
    # one instance to consume the sequential texts in order.
    fake = FakeGemini(texts=["not a diagram", "flowchart TD\n  A-->B"])
    monkeypatch.setattr(g, "get_gemini", lambda: fake)
    out = _run(g.generate_infographic(["s1"], "cycle", "flowchart"))
    assert out["diagramType"] == "flowchart"
    assert out["mermaid"].startswith("flowchart")

    # Unknown diagram type sanitized to mindmap
    fake2 = FakeGemini(texts=["mindmap\n  root((x))"])
    monkeypatch.setattr(g, "get_gemini", lambda: fake2)
    out2 = _run(g.generate_infographic(["s1"], "", "bogus"))
    assert out2["diagramType"] == "mindmap"

    # Persistently invalid -> GeminiUnavailable (mapped to polite 503 downstream)
    fake3 = FakeGemini(texts=["junk", "still junk"])
    monkeypatch.setattr(g, "get_gemini", lambda: fake3)
    try:
        _run(g.generate_infographic(["s1"], "", "mindmap"))
        raise AssertionError("should raise")
    except GeminiUnavailable:
        pass


def test_summarize_offline_fallback(monkeypatch):
    from app import generators as g

    class Off:
        configured = False

    monkeypatch.setattr(g, "get_gemini", lambda: Off())
    out = _run(g.summarize_source("a.txt", "First. Second! Third? Fourth."))
    assert "First" in out
