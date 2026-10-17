# Educator

A local study assistant. It turns PDFs, notes, web articles, YouTube transcripts, images, and audio into a cited tutor, plus podcasts, quizzes, and diagrams.

The interface is three columns:

- **Sources** — ingest files and links, then check the ones that may be used.
- **Tutor** — a Google ADK ReAct agent. Answers cite passages as `[Source: filename, Chunk: id]`.
- **Studio** — multi-speaker podcasts, active-recall quizzes, and Mermaid diagrams.

There is no login. It is a single-user app on your machine. Provider keys stay in `backend/.env` and are never sent to the browser.

## Stack

- Frontend : React, Vite, TypeScript
- Backend: Python FastAPI
- Rag : Hybrid dense (FastEmbed cosine) + async BM25 fused with RRF rerank, top-k from the fused order (chat top-6, studio top-8)
- Google ADK `LlmAgent` (`gemini-3.5-flash-lite`) with tools, not a hand-rolled loop
- SQLite for sources, sessions, and messages
- Qdrant local storage + FastEmbed `BAAI/bge-small-en-v1.5`
- Gemini TTS `gemini-3.1-flash-tts-preview` (Joe / Puck, Jane / Kore, 24 kHz)
- Opik traces via `OpikTracer` and `track_adk_agent_recursive`

### Initial Setup (once)

Needs Node 20+, use `python3.11` or `python3.12` for the virtualenvironment. 'git clone "this repo"'

```bash
# 1. Backend key
cd backend
cp .env.example .env
```
Set `GEMINI_API_KEY` in `backend/.env`. Never commit it.

```bash
# 2. Virtual environment + requirements
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Embedding model (downloads embedding/sentence_similarity model)(cached in .venv, skipped if present)
python scripts/download_model.py

# 4. Frontend
cd ../frontend
npm install
```

### Launch (every time)

Terminal 1 — backend:

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

Terminal 2 — frontend:

```bash
cd frontend
npm run dev
```

Open http://localhost:5173.

### Notes

* Qdrant and SQLite live under `backend/data/` (git-ignored). Never run two API processes at once — the Qdrant file lock will fail.
* Health: `GET http://127.0.0.1:8000/api/health`.

## How retrieval is scoped

There is one physical collection, `educator_sources`. Each source is isolated by `sourceId`. A query always applies a Qdrant `MatchAny` filter, which is the `$in` operator, over the checked source ids.

- Muted sources are not in that list, so they cannot appear in hits.
- An empty selection does not search. It returns nothing.
- Chunks are token-aware and capped at 480 tokens with about 64 tokens of overlap, because `bge-small-en-v1.5` truncates at 512.
- Vectors use binary quantization. Payloads are stored on disk.

Citations in an answer are kept only when the filename and chunk id were actually retrieved for that turn.

## Agent tools

| Tool | Role |
|---|---|
| `rag_retriever` | Vector search limited to the checked sources |
| `web_search` | Google Search grounding, wrapped in its own ADK agent so it can sit beside function tools |
| `podcast_generator` | Dialogue plus multi-speaker speech |
| `quiz_generator` | Structured quiz and flashcards (`responseSchema` / `Type.OBJECT`) |
| `infographic_creator` | Mermaid markup |

The tutor is instructed to cite sources, to say when the selected materials do not contain the answer, and not to invent citations. Web results are labeled as web knowledge, not as file citations.

Opik records the full ADK event stream, including the final response. There is no Ragas pipeline. If `OPIK_API_KEY` and `OPIK_URL_OVERRIDE` are both empty, the tracer is still attached and outbound shipping is disabled so a missing key does not stop a study session.

## Evaluation

Golden set (`golden_dataset.jsonl` → Opik dataset `Rag_eval`, 20 items) scored with Opik-native judges
(`Hallucination`, `AnswerRelevance`, `ContextRecall`, `ContextPrecision`, judge `gemini-3.5-flash-lite`).
Runner: `backend/scripts/evaluate_rag.py`. Full report: `EVAL_REPORT.md`.

| Metric | Avg | Meaning |
|---|---|---|
| hallucination (0 = clean) | 0.000 | No hallucinated answers |
| answer_relevance | 1.000 | Every answer addressed the question |
| context_precision | 1.000 | Retrieved chunks were on-topic |
| context_recall | 0.850 | Some expected facts missing from retrieved context |

Experiment: https://www.comet.com/opik/gunfighter9901/experiments/01a14928-4267-7247-920b-1358f9f7ae32/compare?experiments=%5B%2201a14992-633c-749b-a7f3-e0020cb27101%22%5D

Interpretation: the tutor is grounded — in-domain questions are answered from retrieved chunks, and
out-of-domain questions get an honest "context does not contain the answer" instead of a guess.
Recall is 0.85 rather than 1.0 because the ingested sources don't cover the golden human-rights topics;
the gap measures missing knowledge, not a broken retriever.

## Keys

Put secrets only in `backend/.env`. The repository ships `backend/.env.example` with empty placeholders. Do not print, scan, or commit real keys.
