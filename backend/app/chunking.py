"""Token-aware recursive chunking for BAAI/bge-small-en-v1.5.

The embedding model truncates at 512 wordpiece tokens. Chunks are capped at
480 tokens with about 64 tokens of overlap so the special tokens still fit.
"""

from __future__ import annotations

from dataclasses import dataclass

from huggingface_hub import hf_hub_download
from tokenizers import Tokenizer

from app.config import settings


@dataclass(frozen=True)
class TextChunk:
    text: str
    chunk_index: int
    page_number: int | None
    token_count: int


class TokenChunker:
    def __init__(self, model_name: str | None = None) -> None:
        name = model_name or settings.embedding_model
        path = hf_hub_download(name, "tokenizer.json")
        self.tokenizer = Tokenizer.from_file(path)
        self.max_tokens = settings.chunk_tokens
        self.overlap = settings.chunk_overlap

    def count(self, text: str) -> int:
        if not text:
            return 0
        return len(self.tokenizer.encode(text, add_special_tokens=False).ids)

    def chunk_document(self, pages: list[tuple[int | None, str]]) -> list[TextChunk]:
        chunks: list[TextChunk] = []
        for page_number, text in pages:
            cleaned = _normalize(text)
            if not cleaned:
                continue
            for piece in self._split(cleaned):
                piece = piece.strip()
                if not piece:
                    continue
                chunks.append(
                    TextChunk(
                        text=piece,
                        chunk_index=len(chunks),
                        page_number=page_number,
                        token_count=self.count(piece),
                    )
                )
        return chunks

    def _split(self, text: str) -> list[str]:
        packed = self._pack(self._pieces(text))
        if not packed:
            return []
        return self._apply_overlap(packed)

    def _pieces(self, text: str) -> list[str]:
        """Break text on paragraph, line, then sentence boundaries."""
        blocks = [block.strip() for block in text.split("\n\n") if block.strip()]
        pieces: list[str] = []
        for block in blocks:
            if self.count(block) <= self.max_tokens:
                pieces.append(block)
                continue
            for line in [line.strip() for line in block.split("\n") if line.strip()]:
                if self.count(line) <= self.max_tokens:
                    pieces.append(line)
                    continue
                pieces.extend(self._split_sentences(line))
        return pieces

    def _split_sentences(self, text: str) -> list[str]:
        rough: list[str] = []
        buf = ""
        for char in text:
            buf += char
            if char in ".!?" and self.count(buf) > 40:
                rough.append(buf.strip())
                buf = ""
        if buf.strip():
            rough.append(buf.strip())
        pieces: list[str] = []
        for sentence in rough:
            if self.count(sentence) <= self.max_tokens:
                pieces.append(sentence)
            else:
                pieces.extend(self._windows(sentence))
        return pieces

    def _windows(self, text: str) -> list[str]:
        ids = self.tokenizer.encode(text, add_special_tokens=False).ids
        if not ids:
            return []
        size = self.max_tokens
        step = max(1, size - self.overlap)
        windows: list[str] = []
        for start in range(0, len(ids), step):
            window = ids[start : start + size]
            decoded = self.tokenizer.decode(window).strip()
            if decoded:
                windows.append(decoded)
            if start + size >= len(ids):
                break
        return windows

    def _pack(self, pieces: list[str]) -> list[str]:
        packed: list[str] = []
        current: list[str] = []
        tokens = 0
        for piece in pieces:
            count = self.count(piece)
            if current and tokens + count + 1 > self.max_tokens:
                packed.append("\n\n".join(current).strip())
                current = [piece]
                tokens = count
            else:
                current.append(piece)
                tokens += count + (1 if current else 0)
        if current:
            packed.append("\n\n".join(current).strip())
        return [item for item in packed if item]

    def _apply_overlap(self, chunks: list[str]) -> list[str]:
        if len(chunks) < 2 or self.overlap <= 0:
            return chunks
        overlapped = [chunks[0]]
        for index, chunk in enumerate(chunks[1:], start=1):
            # Overlap comes from the original previous chunk, not an already-overlapped copy.
            tail = self._tail(chunks[index - 1])
            if not tail:
                overlapped.append(chunk)
                continue
            merged = f"{tail}\n\n{chunk}".strip()
            overlapped.append(merged if self.count(merged) <= self.max_tokens else chunk)
        return overlapped

    def _tail(self, text: str) -> str:
        ids = self.tokenizer.encode(text, add_special_tokens=False).ids
        if len(ids) <= self.overlap:
            return text.strip()
        return self.tokenizer.decode(ids[-self.overlap :]).strip()


def _normalize(text: str) -> str:
    cleaned = text.replace("\x00", " ").replace("\r\n", "\n").replace("\r", "\n")
    lines = [" ".join(line.split()) for line in cleaned.split("\n")]
    collapsed: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if not blank:
                collapsed.append("")
            blank = True
            continue
        blank = False
        collapsed.append(line)
    return "\n".join(collapsed).strip()


_chunker: TokenChunker | None = None


def get_chunker() -> TokenChunker:
    global _chunker
    if _chunker is None:
        _chunker = TokenChunker()
    return _chunker
