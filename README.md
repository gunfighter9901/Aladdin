# Educator

A local study assistant. It turns PDFs, notes, web articles, YouTube transcripts, images, and audio into a cited tutor, plus podcasts, quizzes, and diagrams.

The interface is three columns:

- **Sources** — ingest files and links, then check the ones that may be used.
- **Tutor** — a Google ADK ReAct agent. Answers cite passages as `[Source: filename, Chunk: id]`.
- **Studio** — multi-speaker podcasts, active-recall quizzes, and Mermaid diagrams.

There is no login. It is a single-user app on your machine. Provider keys stay in `backend/.env` and are never sent to the browser.

## Stack

- React, Vite, TypeScript
- Python FastAPI
- Google ADK `LlmAgent` (`gemini-3.1-flash-lite`) with tools, not a hand-rolled loop
- SQLite for sources, sessions, and messages
- Qdrant local storage + FastEmbed `BAAI/bge-small-en-v1.5`
- Gemini TTS `gemini-3.1-flash-tts-preview` (Joe / Puck, Jane / Kore, 24 kHz)
- Opik traces via `OpikTracer` and `track_adk_agent_recursive`

## Setup

Needs Python 3.10+ and Node 20+. On macOS with system Python 3.9, use `python3.11` or `python3.12` for the virtualenv.

### Initial setup (once)

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

# 3. Embedding model (cached in .venv, skipped if present)
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

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/upload-source` | File, URL, or pasted text |
| `GET` | `/api/sources` | Sources, summaries, chunk counts |
| `PATCH` | `/api/sources/{id}` | Include or mute a source |
| `DELETE` | `/api/sources/{id}` | Delete the source and its points |
| `GET` | `/api/collection` | The single collection |
| `POST` | `/api/chat` | ReAct turn, citations, trace, artifacts |
| `POST` | `/api/chat/stream` | Same turn as server-sent events |
| `POST` | `/api/generate-podcast` | Script and 24 kHz audio |
| `POST` | `/api/generate-quiz` | Quiz and flashcards |
| `POST` | `/api/generate-infographic` | Mermaid diagram |
| `GET` | `/api/sessions` | Study sessions |
| `POST` | `/api/sessions` | Create a session |
| `GET` | `/api/sessions/{id}` | Messages and studio artifacts |
| `GET` | `/api/health` | Ready state. Booleans only, no secrets |

## Keys

Put secrets only in `backend/.env`. The repository ships `backend/.env.example` with empty placeholders. Do not print, scan, or commit real keys.
