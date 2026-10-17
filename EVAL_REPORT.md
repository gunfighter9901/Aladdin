# Eval Report — Educator (RAG + Agentic + Performance)

Date: 2026-10-17. Codebase read-only, no code changes.
Env: macOS, Python 3.12.10 (`backend/.venv`), frontend `dist/` 3.8M.
Judge for RAG scores: `gemini-3.5-flash-lite` (via LiteLLM). Golden dataset: `golden_dataset.jsonl` → Opik dataset `Rag_eval` (20 items). Runner: `backend/scripts/evaluate_rag.py`.

## 1. RAG settings

- Retrieval: hybrid — dense (Qdrant cosine, FastEmbed `BAAI/bge-small-en-v1.5`, binary quantization, on-disk payload) + async BM25 (`k1=1.5`, `b=0.75`, up to 1000 `$in` candidates, `backend/app/bm25.py` + `VectorStore.abm25_search`).
- Rerank: RRF (`k=60`, `dense_w=1.0`, `bm25_w=1.0`). Each branch over-fetches 20, fused order is final (`VectorStore.search` / `ahybrid_search`).
- Top-k: chat `retrieval_limit`=6 (1–20); studio top-8 (+ sample-8 fallback).
- Chunking: 480 tokens + 64 overlap. One collection `educator_sources`, isolated by `sourceId` (`$in`); empty selection retrieves nothing.

## 2. Golden-dataset results (20/20, 0 failures)

> Scores measured with previous dense-only retriever. Re-run `scripts/evaluate_rag.py` for hybrid + RRF.

| Metric | Avg | Meaning |
|---|---|---|
| hallucination (0 = clean) | 0.000 | No hallucinated answers |
| answer_relevance | 1.000 | Every answer addressed the question |
| context_precision | 1.000 | Retrieved chunks on-topic |
| context_recall | 0.850 | Some expected facts missing from context |

Experiment: https://www.comet.com/opik/gunfighter9901/experiments/01a14928-4267-7247-920b-1358f9f7ae32/compare?experiments=%5B%2201a14992-633c-749b-a7f3-e0020cb27101%22%5D

## 3. Live showcase (same RAG, 6 chunks each)

| Query | Answer | Hall. | Rel. | Recall | Prec. |
|---|---|---|---|---|---|
| Undo last commit, keep changes? | `git reset HEAD~1`. | 0.0 | 1.0 | 1.0 | 1.0 |
| Welding for sheet-metal? | MIG, TIG, spot, projection, laser. | 0.0 | 1.0 | 1.0 | 1.0 |
| Capital of France? | Context does not contain the answer. | 0.0 | 1.0 | 1.0 | 1.0 |

Interpretation: grounded in-domain, honest OOD abstention. Recall 0.85 = local sources lack golden human-rights topics, not broken retriever.

## 4. Agentic Metrics (Autonomy & Problem Solving)

Offline probes (real tokenizer, FakeStore, no LLM bulk eval — 15 req/min free tier, 20×5 calls too costly) + `pytest`:

| Metric | Result | Proves |
|---|---|---|
| Suite | 40/40 in ~2.3s | No regressions |
| Citation fidelity | 1 kept, hallucinated `fake-c9` dropped | Only retrieved `(filename,chunkId)` emitted |
| Scope grounding | scoped lists `bio.txt/s1` + `only these`; empty → `none` + `Do not invent citations` | Muted/unselected never retrieved |
| Isolation | `Filter(key=sourceId, MatchAny{a,b})` + post-filter | `$in` enforced |
| Abstention | empty → `AppError Select at least one`; OOD → `context does not contain` | Guardrail |
| Studio schema | bad quiz options dropped, podcast TTS-fail → `{audioBase64:"",audioError}`, mermaid strip + 1 repair, unknown → mindmap (5/5 validators) | Schema-valid outputs |
| Safety | `../../etc/passwd→passwd`, `RuntimeError→Something went wrong`, `AIza` redacted, SSRF rejects `localhost/127/ftp` | No leak |
| Autonomy (static) | 1 ADK `LlmAgent` + 5 tools (`rag_retriever`, `web_search`, `podcast/quiz/infographic`); ContextVars + `tool_context.state`, dual-written artifacts, full stream for Opik | ReAct loop |
| Local knowledge | SQLite 4 sources `ready`; Qdrant 58 points | Read-only count |

Marketing: answers cite checked sources, abstains when missing, never invents citations.

## 5. Performance & Speed (UX)

API via `TestClient` + FakeStore (local overhead, LLM excluded), n=50:

| Endpoint | p50 | p95 | p99/max |
|---|---|---|---|
| `GET /api/health` | 0.59ms | 0.80ms | 6.73ms |
| `GET /api/collection` | 0.60ms | 0.70ms | 0.84ms |
| `GET /api/sources` | 1.12ms | 1.30ms | 2.79ms |
| `GET /api/sessions` | 1.11ms | 1.33ms | 2.92ms |
| `POST /api/chat` validation (422) | 1.73ms (1x) | — | — |
| `POST /api/upload-source` text (live Gemini summary) | 1276ms (1x) | — | — |

Primitives (CPU after warm):

| Stage | Result |
|---|---|
| Tokenizer warm | 740.8ms once (cached) |
| Chunk 30 pages → 30 chunks | 0.6ms, ~51k chunks/s, all ≤480 tok |
| BM25 1000 docs | 1.3ms (common), 0.4ms (rare) |
| RRF 20+20 | 0.004ms |

Build/static:

| Check | Result |
|---|---|
| `pytest` | 40 passed |
| `tsc --noEmit` | 0 errors, 0.85s |
| `compileall backend/app` | ok |
| `dist/` | 3.8M, `index-*.js` 240K |

Marketing: reads <2ms p50; chunk/BM25/RRF sub-2ms; uploads ~1.3s (Gemini summary).

## 6. API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/upload-source` | File, URL, or pasted text |
| `GET` | `/api/sources` | Sources, summaries, chunk counts |
| `PATCH` | `/api/sources/{id}` | Include/mute |
| `DELETE` | `/api/sources/{id}` | Delete + points |
| `GET` | `/api/collection` | Single collection |
| `POST` | `/api/chat` | ReAct turn, citations, trace |
| `POST` | `/api/chat/stream` | Same as SSE |
| `POST` | `/api/generate-podcast` | Script + 24kHz audio |
| `POST` | `/api/generate-quiz` | Quiz + flashcards |
| `POST` | `/api/generate-infographic` | Mermaid |
| `GET` | `/api/sessions` | List |
| `POST` | `/api/sessions` | Create |
| `GET` | `/api/sessions/{id}` | Messages + artifacts |
| `GET` | `/api/health` | Ready, booleans only |


Limits: no live ADK TTFT/TTS timing (quota); no k6/Lighthouse; RAG scores pre-hybrid; single-process Qdrant respected.
