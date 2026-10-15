# NotebookLM / AI Research Assistant - Project Plan & Specification,Architectural Blueprint: Local Agentic RAG Educator & Research Assistant

The **Agentic RAG Educator & Research Assistant** represents a state-of-the-art local artificial intelligence platform that converts static study materials (PDFs, Markdown, Web Articles, YouTube Transcripts, Images, and Audio) into multi-modal interactive learning artifacts.

The system operates as a lightweight, decoupled local runtime. The application features a **3-Column Architecture**:
1. **Left Panel (Sources & Dynamic Vector Collections)**: Ingestion of multimodal files, source organization, dynamic metadata filtering (`$in` operator), source summaries, and collection lifecycle management.
2. **Center Panel (Agentic ReAct Loop & Conversational Engine)**: A Google ADK ReAct agent with function calling, inline citation tracking (`[Source: filename, Chunk: ID]`), web search grounding fallback, and full Opik trace logging.
3. **Right Panel (Media, Active-Recall Quizzes & Visual Infographics)**: Multi-speaker audio podcast synthesis (`gemini-3.1-flash-tts-preview`), structured JSON quiz and flashcard generator, and live Mermaid.js mindmap/diagram visualizer.

---

## System Overview

The **Agentic RAG Educator & AI Research Assistant** is a full-stack web application designed for interactive learning, content transformation, and research synthesis. It uses a **ReAct Agent Architecture**, **Isolated Vector Collections**, and **Dynamic Source Filtering (`$in` operator)** to provide accurate, citation-backed answers, multi-speaker podcasts, structured quizzes, and visual diagrams.

---

##  Key Architecture Features

1. **3-Column Layout**:
   - **Left Column**: Multimodal Ingestion (PDF, MD, TXT, Web URLs, YouTube links, Images) + Source Selector + Collection Manager.
   - **Center Column**: Multi-session Chat + Google ADK ReAct trace (Opik) + Inline Citation Markers.
   - **Right Column**: Media & Content Generation Suite (Audio Podcasts with Gemini TTS, Active Recall Quizzes & Flashcards, Mermaid Mindmaps).

2. **ReAct Router Agent & Tools**:
   - `rag_retriever`: vector retriever filtered by selected sources.
   - `web_search`: Google Search grounding tool for general web knowledge.
   - `podcast_generator`: Script generation + `gemini-3.1-flash-tts-preview` multi-speaker speech synthesis.
   - `quiz_generator`: Structured JSON quiz & flashcard schema generator.
   - `infographic_creator`: Mermaid.js markup visualizer.

3. **Opik Observability**:
   - Google ADK runs are fully traced with Opik (`OpikTracer` + `track_adk_agent_recursive`).
   - Every agent interaction logs step execution time, tool parameters, prompt reasoning, and outputs.
   - No Ragas evaluation pipeline.

---

##  Technology Stack

- **Frontend**: React, Vite, TypeScript
- **Backend**: Python FastAPI. No Express, no Node server.
- **Agent**: Google ADK (`google-adk`) ReAct tool loop. Not a hand-rolled agent loop.
- **App data**: SQLite for sources, sessions, and messages.
- **Vectors**: Qdrant + FastEmbed `BAAI/bge-small-en-v1.5` (local CPU embeddings).
- **AI Models (Gemini API)**:
  - Text, agent reasoning, and image transcription: `gemini-3.1-flash-lite`
  - Text-to-Speech (Podcasts): `gemini-3.1-flash-tts-preview` with multi-speaker voice config
  - Web grounding: ADK Google Search tool
- **Observability**: Opik full traces only. No Ragas.
- **Auth**: none. Single-user local app.

## High-Level System Architecture & Execution Blueprint

```
+-----------------------------------------------------------------------------------+
|                        AGENTIC RAG EDUCATOR WEB INTERFACE                         |
|   +-----------------------+   +-----------------------+   +-------------------+   |
|   |   Sources Side-Panel  |   |   Central Chat Window |   | Multi-Media Panel |   |
|   | (Docs, Links, Media)  |   | (Citations & Streams) |   | (Audio/Quiz/Visual|   |
|   +-----------+-----------+   +-----------+-----------+   +---------+---------+   |
+---------------+---------------------------|-------------------------+-------------+
                |                           |                         |
                v                           v                         v
+------------------------+   +-----------------------+   +-------------------+
|   Ingestion Pipeline   |   |   Router Agent Loop   |   | Session Memory /  |
| (Extract, Chunk, Embed)|   |  (ADK ReAct / LLM)    |   | Opik Tracing      |
+-----------+------------+   +-----------+-----------+   +-------------------+
            |                            |
            v                            +-----------------------------------+
+------------------------+               |               |                   |
| Isolated Vector Store  |               v               v                   v
| +--------------------+ |     +------------------+ +----------+ +-----------------+
| | Source 1 Collection| | <-> | Retrieval Tools  | | Audio Gen| | Quiz & Visuals  |
| | Source 2 Collection| |     | (Per Source)     | | Pipeline | | Pipelines       |
| +--------------------+ |     +------------------+ +----------+ +-----------------+
+------------------------+
```

---

## Modular Component Specifications

### 1. Ingestion Pipeline & Isolated Vector Storage (`backend/app/vectorstore.py`, `backend/app/ingestion.py`)
- **Multimodal Text Extraction**:
  - Documents (PDF, Markdown, TXT) parsed with recursive character chunking (default chunk size: 800 tokens, overlap: 150 tokens).
  - Images transcribed using Gemini Vision model (`gemini-3.6-flash`).
  - Web Articles scraped and cleaned; YouTube links parsed for video transcripts.
- **Isolated Vector Collections**:
  - Each source gets an isolated collection with custom metadata: `sourceId`, `filename`, `pageNumber`, `chunkIndex`, and `createdTime`.
  - Dynamic filtering uses the `$in` operator: queries strictly search within the user's selected active sources (`active_sources`).

### 2. Google ADK ReAct Agent & Opik
- **Agent**: one ADK `LlmAgent` (`gemini-3.1-flash-lite`) with these tools:
  1. Analyzes the user prompt against conversation history and the selected sources.
  2. Calls tools as needed:
     - `rag_retriever`: Vector search with a `$in` filter on active source ids.
     - `web_search`: Google Search grounding when local source knowledge is insufficient.
     - `podcast_generator`: Dialogue script plus Gemini TTS synthesis.
     - `quiz_generator`: Structured JSON active-recall quizzes and flashcards.
     - `infographic_creator`: Valid Mermaid.js markup.
  3. Synthesizes output with mandatory inline source citations `[Source: filename, Chunk: id]`.
- **Opik**: instrument the agent with `track_adk_agent_recursive` and `OpikTracer`. Consume the full ADK event stream so traces include the final response. No Ragas wrapper.

### 3. Asynchronous Audio & Podcast Pipeline 
- Converts source context into a multi-speaker dialogue script (e.g. Host "Joe" & Expert "Jane").
- Invokes `gemini-3.1-flash-tts-preview` with `multiSpeakerVoiceConfig` (`Puck` and `Kore` voices) to output raw 24kHz audio base64 stream.

### 4. Active-Recall Quiz & Flashcards Pipeline 
- Uses Gemini `responseSchema` with `Type.OBJECT` to guarantee structured JSON output.
- Returns question items, option choices, correct answer keys, and pedagogical rationale.

### 5. Visual Infographics & Mindmaps Pipeline 
- Synthesizes complex concepts into Mermaid.js diagrams (mindmaps, flowcharts, timelines, sequence diagrams).

---

# Suggested High level project structure 
my-project/
├── backend/
│   ├── app/
│   │   ├── main.py          # App initialization, middleware, routes
│   │   ├── config.py        # Environment variables & settings
│   │   ├── database.py      # SQLite / database session setup
│   │   ├── models.py        # SQLAlchemy / SQLModel tables
│   │   ├── schemas.py       # Pydantic request/response models
│   │   └── routers/         # Endpoint modules (e.g., items.py, users.py)
│   ├── requirements.txt
│   └── .env
│
├── frontend/
│   ├── src/
│   │   ├── assets/          # Static assets & icons
│   │   ├── components/      # Shared, reusable UI components (Button, Modal)
│   │   ├── features/        # Feature-based logic (e.g., auth/, dashboard/)
│   │   │   └── items/       # Component, API call, and state in one place
│   │   │       ├── ItemList.jsx
│   │   │       └── itemApi.js
│   │   ├── App.jsx
│   │   └── main.jsx
│   ├── vite.config.js       # Proxy setup for backend communication
│   └── package.json
│
└── README.md

## API Endpoint Reference

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/upload-source` | Uploads file, URL, or text, extracts chunks, builds embeddings, and creates vector collection |
| `GET` | `/api/sources` | Returns all ingested sources with summaries and chunk counts |
| `DELETE` | `/api/sources/:id` | Deletes source vector collection and unregisters its tool |
| `POST` | `/api/chat` | Main ReAct Agent chat loop with dynamic vector filtering, tool invocation, and trace logging |
| `POST` | `/api/generate-podcast` | Generates script and multi-speaker audio base64 via Gemini TTS |
| `POST` | `/api/generate-quiz` | Generates active recall quizzes and flashcards as structured JSON |
| `POST` | `/api/generate-infographic` | Generates Mermaid.js diagram markup from context |
| `GET` | `/api/sessions` | Lists active chat sessions |
| `POST` | `/api/sessions` | Creates or switches active chat session |

---

## Locked decisions
- Agent runtime is Google ADK, not a custom ReAct loop and not LangChain.
- Observability is Opik full traces only. Do not add a Ragas script or wrapper. `evaluation_observability.md` is reference only; ignore its Ragas sections.
- Backend is Python FastAPI. Frontend is React + Vite. No auth.
- Reasoning and image transcription model is `gemini-3.1-flash-lite`.

## additional info
- Embeddings and storage follow `embedding_vectorstore.md`: FastEmbed `BAAI/bge-small-en-v1.5`, Qdrant, binary quantization, on-disk payload. Chunking is token-aware and capped at 480 tokens with about 64 tokens of overlap, because this embedding model truncates at 512.
- Keys live in `.env`. Never scan env keys, never print keys, never write real keys into the repo. Ship `.env.example` placeholders.
- Isolation. One physical Qdrant collection. Each source is isolated by `sourceId`. Retrieval uses a `$in` list of active source ids. Muted sources must not appear in hits. An empty selection retrieves nothing.
- Citations. Synthesized answers include [Source: filename, Chunk: id]. Citation objects include sourceId, filename, chunkId.
- Guardrails. System prompt suitable for students learning from the application: tutor, cite sources, say when the material does not contain the answer.
- After the build is complete, analyze `testing.md` only if the user explicitly directs and approves testing. Do not test during the build.
---
## Conclusion
This architectural blueprint ensures an enterprise-grade, local agentic RAG educator system with total transparency, dynamic source scoping, and rich multi-modal output.
