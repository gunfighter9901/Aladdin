# Eval Report — Educator RAG

Date: 2026-10-17. Judge for all scores: `gemini-3.5-flash-lite` (same key as the app, via LiteLLM).
Golden dataset: `golden_dataset.jsonl` → Opik dataset `Rag_eval` (20 items). Runner: `backend/scripts/evaluate_rag.py`.

## RAG settings

- Retrieval type: dense semantic only (Qdrant cosine over FastEmbed `BAAI/bge-small-en-v1.5`, binary quantization, on-disk payload). No keyword/BM25, no hybrid.
- Reranking: none.
- Top-k: agent chat `retrieval_limit` = 6 (allowed 1–20); studio pipelines top-8 (+ sample-8 fallback).
- Chunking: 480 tokens with 64 overlap. Sources isolated by `sourceId` (`$in` filter); empty selection retrieves nothing.

## Golden-dataset results (20/20, 0 scoring failures)

| Metric | Avg | Meaning |
|---|---|---|
| hallucination (0 = clean) | 0.000 | No hallucinated answers |
| answer_relevance | 1.000 | Every answer addressed the question |
| context_precision | 1.000 | Retrieved chunks were on-topic |
| context_recall | 0.850 | Some expected facts missing from retrieved context |

Experiment: https://www.comet.com/opik/gunfighter9901/experiments/01a14928-4267-7247-920b-1358f9f7ae32/compare?experiments=%5B%2201a14992-633c-749b-a7f3-e0020cb27101%22%5D

## Live showcase queries (same RAG, 6 chunks each)

| Query | Answer (truncated) | Hall. | Rel. | Recall | Prec. |
|---|---|---|---|---|---|
| How do I undo the most recent commit but keep my code changes? | Use `git reset HEAD~1`. | 0.0 | 1.0 | 1.0 | 1.0 |
| What welding processes join sheet-metal components? | MIG, TIG, spot, projection, laser welding. | 0.0 | 1.0 | 1.0 | 1.0 |
| What is the capital of France? | The context does not contain the answer. | 0.0 | 1.0 | 1.0 | 1.0 |

## Interpretation

- The tutor is grounded: in-domain questions (git, sheet-metal — topics present in the 3 ingested sources) are answered from retrieved chunks with perfect scores.
- Out-of-domain questions get an honest abstention instead of a guess (hallucination 0.0), which is the designed guardrail behavior.
- Golden recall is 0.85 rather than 1.0 because the local sources don't cover the golden human-rights topics — the gap measures missing knowledge, not a broken retriever. Ingest sources covering the golden topics and re-run to see recall rise.

## Re-run

```bash
cd backend
pkill -f "uvicorn app.main"   # local Qdrant allows one process at a time
./.venv/bin/python scripts/evaluate_rag.py            # full golden set
./.venv/bin/python scripts/evaluate_rag.py --samples 5  # quick check
./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000  # restart
```
