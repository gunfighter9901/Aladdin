"""Async BM25 keyword search + RRF hybrid fusion.

Stdlib only (no extra model download). Tokenization is intentionally simple:
lowercase alphanumeric tokens, so dense (FastEmbed) and sparse (BM25) fail
in different ways and complement each other.

- ``tokenize``: shared tokenizer for query + chunks.
- ``BM25``: Okapi BM25 over an in-memory corpus (the $in-filtered candidates).
- ``rrf_fuse``: Reciprocal Rank Fusion. Simple, parameter-light rerank whose
  output order IS the final ranking -- the backend then takes top-k.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Very small stopword list: keeps exact-phrase/rare-term signals while
# preventing "what is the" from dominating BM25 scores.
_STOPWORDS = frozenset(
    {
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
        "and", "or", "but", "if", "then", "else", "when", "at", "by",
        "for", "with", "about", "into", "through", "during", "of", "to",
        "in", "on", "that", "this", "it", "as", "from", "what", "which",
        "who", "whom", "how", "why", "where", "do", "does", "did", "can",
        "could", "should", "would", "may", "might", "will", "shall",
    }
)


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens, stopwords removed."""
    tokens = _TOKEN_RE.findall((text or "").lower())
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 1]


class BM25:
    """Okapi BM25 over a fixed corpus. Corpus = $in-filtered candidates."""

    def __init__(
        self,
        docs_tokens: list[list[str]],
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self.k1 = float(k1)
        self.b = float(b)
        self.docs_tokens = docs_tokens
        self.n_docs = len(docs_tokens)
        self.doc_lens = [len(d) for d in docs_tokens]
        self.avgdl = sum(self.doc_lens) / self.n_docs if self.n_docs else 0.0
        # Document frequency per term.
        df: Counter[str] = Counter()
        for tokens in docs_tokens:
            for term in set(tokens):
                df[term] += 1
        self.df = df
        # Per-doc term frequencies.
        self.doc_tf: list[Counter[str]] = [Counter(d) for d in docs_tokens]
        # Precompute IDF (Lucene variant, never negative).
        self.idf: dict[str, float] = {}
        n = max(1, self.n_docs)
        for term, freq in df.items():
            self.idf[term] = math.log(1.0 + (n - freq + 0.5) / (freq + 0.5))

    def score(self, query_tokens: list[str]) -> list[float]:
        """Score every doc against the tokenized query."""
        if not self.n_docs or not query_tokens:
            return [0.0] * self.n_docs
        qtf = Counter(query_tokens)
        scores = [0.0] * self.n_docs
        for term, qcount in qtf.items():
            idf = self.idf.get(term)
            if idf is None:
                continue
            for idx in range(self.n_docs):
                tf = self.doc_tf[idx].get(term, 0)
                if not tf:
                    continue
                dl = self.doc_lens[idx] or 1
                denom = tf + self.k1 * (1.0 - self.b + self.b * dl / max(1e-9, self.avgdl or 1.0))
                scores[idx] += idf * (tf * (self.k1 + 1.0) / denom) * min(qcount, 3)
        return scores


def rrf_fuse(
    dense_rank: dict[Any, int],
    bm25_rank: dict[Any, int],
    *,
    k: int = 60,
    dense_weight: float = 1.0,
    bm25_weight: float = 1.0,
) -> dict[Any, float]:
    """Reciprocal Rank Fusion. Ranks are 1-based. Missing = no contribution.

    Returns {key: fused_score}. Higher is better. This fused order is the
    final ranking; the caller takes top-k.
    """
    fused: dict[Any, float] = {}
    for key, rank in dense_rank.items():
        fused[key] = fused.get(key, 0.0) + dense_weight / (k + rank)
    for key, rank in bm25_rank.items():
        fused[key] = fused.get(key, 0.0) + bm25_weight / (k + rank)
    return fused
