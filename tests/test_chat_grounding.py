"""Chat grounding: citations kept only for retrieved hits, scope catalog, trace shrink.

Covers backend/app/chat_runner.py pure helpers:
- CITATION_RE + _citations: hallucinated citations dropped, deduped,
  case-insensitive, keyed by (source_id, chunk_id)
- _agent_message: strict source catalog; empty selection forbids invention
- _selected_sources: only status==ready, deduped, capped
- _shrink: hits text stripped from traces (Opik stays small)
"""
from __future__ import annotations


def _hit(source_id="s1", filename="notes.txt", chunk_id="s1-c0", text="light reactions"):
    from app.vectorstore import Hit

    return Hit(source_id, filename, chunk_id, 0, None, text, 0.9)


def test_citations_keep_only_retrieved_hits():
    from app.chat_runner import _citations

    hits = [_hit(), _hit("s2", "other.txt", "s2-c3", "dark reactions")]
    text = (
        "Claim one [Source: notes.txt, Chunk: s1-c0] and "
        "claim two [Source: other.txt, Chunk: s2-c3] plus "
        "invented [Source: fake.txt, Chunk: fake-c9]."
    )
    out = _citations(text, hits)
    assert {(c["sourceId"], c["chunkId"]) for c in out} == {("s1", "s1-c0"), ("s2", "s2-c3")}


def test_citations_dedup_and_case_insensitive():
    from app.chat_runner import _citations

    hits = [_hit(filename="Notes.TXT", chunk_id="S1-C0")]
    text = "[Source: notes.txt, Chunk: s1-c0] again [Source: NOTES.TXT, Chunk: S1-C0]"
    out = _citations(text, hits)
    assert len(out) == 1
    assert out[0]["sourceId"] == "s1"


def test_citations_empty_when_no_hits():
    from app.chat_runner import _citations

    assert _citations("[Source: a.txt, Chunk: a-c0]", []) == []
    assert _citations("no markers here", [_hit()]) == []


def test_agent_message_scopes_retrieval_catalog():
    from app.chat_runner import _agent_message

    scoped = _agent_message("Explain?", [{"id": "s1", "filename": "bio.txt"}])
    assert "bio.txt" in scoped and "s1" in scoped
    assert "only these may be retrieved" in scoped.lower()

    empty = _agent_message("Explain?", [])
    assert "none" in empty.lower()
    assert "Do not invent citations" in empty


def test_selected_sources_only_ready_deduped_capped():
    from sqlmodel import Session

    from app.chat_runner import _selected_sources
    from app.config import settings
    from app.models import Source, utcnow

    # Build an isolated in-memory DB for this unit test
    from sqlmodel import SQLModel, create_engine

    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Source(id="ready-1", filename="a.txt", title="a", kind="text", status="ready"))
        db.add(Source(id="proc-1", filename="b.txt", title="b", kind="text", status="processing"))
        db.add(Source(id="err-1", filename="c.txt", title="c", kind="text", status="error", active=False))
        db.commit()
        got = _selected_sources(db, ["ready-1", "ready-1", "proc-1", "err-1", "missing", ""])
        assert [s.id for s in got] == ["ready-1"]
        # Cap respected
        many = ["ready-1"] * (settings.max_sources_per_query + 5)
        assert len(_selected_sources(db, many)) <= settings.max_sources_per_query


def test_clean_title_single_line_capped():
    from app.chat_runner import _clean_title

    assert _clean_title("  hello\nworld  ") == "hello world"
    assert len(_clean_title("x" * 200)) <= 72
    assert _clean_title("   ") == "Study session"


def test_shrink_strips_hit_text_from_traces():
    from app.chat_runner import _shrink

    big = {"status": "ok", "hits": [{"filename": "a.txt", "chunkId": "a-c0", "sourceId": "s", "pageNumber": None, "score": 1.0, "text": "SECRET " * 500}]}
    out = _shrink(big)
    assert "SECRET" not in str(out)
    assert out["hits"][0]["chunkId"] == "a-c0"

    # Non-hit payloads over 1800 chars become {truncated, preview}
    out2 = _shrink({"blob": "x" * 5000})
    assert out2 == {"truncated": True, "preview": out2["preview"]}
