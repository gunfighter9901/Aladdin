"""Shared fixtures for the Educator test suite.

Isolation strategy (no network, no Gemini, no real Qdrant embeddings):
- OPIK shipping disabled before any app import.
- SQLite replaced with a tmp file per test.
- Vector store replaced with an in-memory FakeStore in every module
  that imported ``get_store`` at import time.
- ADK runtime never started (session_service stays None).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("OPIK_TRACK_DISABLE", "true")
os.environ.pop("OPIK_URL_OVERRIDE", None)

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import SQLModel, create_engine  # noqa: E402


class FakeStore:
    """Minimal in-memory stand-in for VectorStore."""

    def __init__(self) -> None:
        self.points = 0
        self.deleted: list[str] = []
        self.upserted: list[dict] = []
        self.hits: list = []

    def ensure_collection(self) -> None:
        return None

    def count(self) -> int:
        return self.points

    def delete_source(self, source_id: str) -> None:
        self.deleted.append(source_id)

    def upsert_chunks(self, *, source_id: str, filename: str, chunks) -> int:
        self.upserted.append({"source_id": source_id, "filename": filename, "n": len(chunks)})
        self.points += len(chunks)
        return len(chunks)

    def search(self, query: str, source_ids: list[str], limit=None):
        ids = [i for i in (source_ids or []) if i]
        if not ids or not (query or "").strip():
            return []
        return [h for h in self.hits if h.source_id in ids][: (limit or 6)]

    def sample(self, source_ids: list[str], limit: int = 8):
        ids = set(source_ids or [])
        if not ids:
            return []
        out = [h for h in self.hits if h.source_id in ids]
        out.sort(key=lambda h: (h.source_id, h.chunk_index))
        return out[:limit]


@pytest.fixture()
def fake_store():
    return FakeStore()


@pytest.fixture()
def client(tmp_path, monkeypatch, fake_store):
    """FastAPI TestClient with isolated SQLite + FakeStore + no ADK."""
    import app.chat_runner as chat_runner
    import app.database as database
    import app.main as main_module
    import app.routers.sources as sources_router
    from app import models  # noqa: F401 - register tables

    db_file = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    def get_test_session():
        from sqlmodel import Session

        with Session(engine) as session:
            try:
                yield session
            except Exception:
                session.rollback()
                raise

    # Patch every module that bound get_store / engine at import time.
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(chat_runner, "engine", engine)
    monkeypatch.setattr("app.main.get_store", lambda: fake_store)
    monkeypatch.setattr("app.routers.sources.get_store", lambda: fake_store)
    monkeypatch.setattr("app.ingestion.get_store", lambda: fake_store)
    monkeypatch.setattr("app.generators.get_store", lambda: fake_store)
    monkeypatch.setattr("app.vectorstore.get_store", lambda: fake_store)

    # Never touch backend/data/uploads during tests: ingestion writes the
    # original file + extracted.txt per source. Replace with a no-op.
    monkeypatch.setattr("app.ingestion._store_original", lambda *a, **k: None)

    # main.health imports engine lazily, but patch database.engine covers it.
    # Ensure ADK runtime is not ready so session creation skips ADK.
    import app.agent as agent_module

    monkeypatch.setattr(agent_module.runtime, "session_service", None)
    monkeypatch.setattr(agent_module.runtime, "ready", False)
    monkeypatch.setattr(agent_module.runtime, "runner", None)
    monkeypatch.setattr("app.chat_runner.runtime", agent_module.runtime)

    main_module.app.dependency_overrides[database.get_session] = get_test_session
    try:
        with TestClient(main_module.app) as tc:
            tc.fake_store = fake_store  # type: ignore[attr-defined]
            tc.test_engine = engine  # type: ignore[attr-defined]
            yield tc
    finally:
        main_module.app.dependency_overrides.clear()
