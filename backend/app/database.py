"""SQLite sessions for sources, chats, and generated artifacts."""

from __future__ import annotations

from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

engine = create_engine(
    f"sqlite:///{settings.sqlite_path}",
    connect_args={"check_same_thread": False},
)


def init_db() -> None:
    from app import models  # noqa: F401  (register tables)

    SQLModel.metadata.create_all(engine)
    _recover_interrupted()


def _recover_interrupted() -> None:
    """Crash recovery: ingestion is synchronous per-request, so any row still
    marked processing at startup belongs to a request that died with the
    previous process (SIGTERM during deploy/restart). Flip it to error so the
    library never shows a permanent Reading… state."""
    from sqlmodel import select

    from app.models import Source

    with Session(engine) as session:
        stuck = session.exec(select(Source).where(Source.status == "processing")).all()
        if not stuck:
            return
        for row in stuck:
            row.status = "error"
            row.error = "Ingestion was interrupted by a server restart. Try adding it again."
            row.active = False
            session.add(row)
        session.commit()


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
