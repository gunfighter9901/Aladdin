"""Chunking + text utilities: token caps, overlap, truncation, Mermaid guards.

Covers backend/app/chunking.py and the pure helpers in generators.py.
Uses a stub tokenizer so no HuggingFace download is needed.
"""
from __future__ import annotations


class _FakeEncode:
    def __init__(self, ids):
        self.ids = ids


class StubTokenizer:
    """Whitespace tokenizer: 1 token per word."""

    def encode(self, text, add_special_tokens=False):
        n = len((text or "").split())
        return _FakeEncode(list(range(n)))

    def decode(self, ids):
        return " ".join(["tok"] * len(ids))


def _make_chunker(monkeypatch, max_tokens=10, overlap=2, cap=2000):
    from app import chunking as ch

    monkeypatch.setattr(ch, "hf_hub_download", lambda *a, **k: "/tmp/fake.json")
    monkeypatch.setattr(ch.Tokenizer, "from_file", classmethod(lambda cls, p: StubTokenizer()))
    monkeypatch.setattr(ch.settings, "chunk_tokens", max_tokens)
    monkeypatch.setattr(ch.settings, "chunk_overlap", overlap)
    monkeypatch.setattr(ch.settings, "max_chunks_per_source", cap)
    return ch.TokenChunker()


def test_normalize_collapses_whitespace_and_nulls():
    from app.chunking import _normalize

    assert _normalize("a\x00b\r\nc\rd") == "a b\nc\nd"
    assert _normalize("  hello   world  \n\n\nnext ") == "hello world\n\nnext"
    assert _normalize("   \n  ") == ""


def test_chunk_document_caps_tokens_and_indexes(monkeypatch):
    chunker = _make_chunker(monkeypatch, max_tokens=10, overlap=2)
    pages = [(1, " ".join([f"w{i}" for i in range(25)]))]
    chunks = chunker.chunk_document(pages)
    assert len(chunks) >= 2
    for c in chunks:
        assert c.token_count <= 10, f"chunk exceeds cap: {c.token_count}"
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert all(c.page_number == 1 for c in chunks)


def test_chunk_document_respects_max_chunks_cap(monkeypatch):
    chunker = _make_chunker(monkeypatch, max_tokens=5, overlap=0, cap=3)
    pages = [(None, "a b c d e f g h i j k l m n o p")]
    chunks = chunker.chunk_document(pages)
    assert len(chunks) <= 3


def test_chunk_document_skips_empty_pages(monkeypatch):
    chunker = _make_chunker(monkeypatch)
    assert chunker.chunk_document([(1, "   "), (2, "")]) == []


def test_windows_slide_with_overlap(monkeypatch):
    chunker = _make_chunker(monkeypatch, max_tokens=5, overlap=2)
    text = " ".join([f"w{i}" for i in range(12)])
    windows = chunker._windows(text)
    assert len(windows) >= 2
    for w in windows:
        assert chunker.count(w) <= 5


def test_truncate_at_sentence_prefers_boundary():
    from app.generators import _truncate_at_sentence

    text = "First sentence. " + "x" * 200 + "\n\nSecond block here. " + "y" * 200
    out = _truncate_at_sentence(text, 100)
    assert len(out) <= 100
    assert out.endswith((".", "!", "?", "\n")) or " " not in out[len(out) - 10 :]


def test_extractive_summary_falls_back_to_first_sentences():
    from app.generators import _extractive_summary

    text = "Alpha. Beta! Gamma? Delta."
    out = _extractive_summary(text)
    assert "Alpha" in out
    assert len(out) <= 500


def test_mermaid_clean_and_valid():
    from app.generators import _clean_mermaid, _valid_mermaid

    assert _clean_mermaid("```mermaid\nmindmap\n  root\n```").strip().startswith("mindmap")
    assert _valid_mermaid("mindmap\n  root((idea))") is True
    assert _valid_mermaid("flowchart TD\n  A-->B") is True
    assert _valid_mermaid("sequenceDiagram\n  A->>B: hi") is True
    # Guards: fences, single line, bad start, unbalanced brackets
    assert _valid_mermaid("```mindmap\n  x") is False
    assert _valid_mermaid("mindmap") is False
    assert _valid_mermaid("graphviz\n  A-->B") is False
    assert _valid_mermaid("flowchart TD\n  A[unclosed-->B") is False
    assert _valid_mermaid("x" * 20001) is False
