"""SQLite sessions for sources, chats, and generated artifacts."""

from __future__ import annotations

from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

engine = create_engine(
    f"sqlite:///{settings.sqlite_path}",
    connect_args={"check_same_thread": False, "timeout": 30},
    pool_pre_ping=True,
)


def init_db() -> None:
    from app import models  # noqa: F401  (register tables)

    SQLModel.metadata.create_all(engine)
    _migrate()
    _recover_interrupted()


def _migrate() -> None:
    """Bring databases created by older code up to the current schema.

    `create_all` only creates missing tables; it never adds a column to an
    existing table. So when the models gain a column (e.g. `sources.updated_at`),
    an existing DB would crash on the first SELECT. Inspect each table and
    ALTER it into shape instead.
    """
    import logging

    from sqlalchemy import text

    logger = logging.getLogger("educator.db")
    with engine.begin() as conn:
        columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(sources)").fetchall()}
        if columns and "updated_at" not in columns:
            conn.exec_driver_sql("ALTER TABLE sources ADD COLUMN updated_at TIMESTAMP")
            conn.exec_driver_sql("UPDATE sources SET updated_at = created_at WHERE updated_at IS NULL")
            logger.info("Migrated sources table: added updated_at")
        for index_sql in (
            "CREATE INDEX IF NOT EXISTS ix_sources_filename ON sources (filename)",
            "CREATE INDEX IF NOT EXISTS ix_sources_kind ON sources (kind)",
            "CREATE INDEX IF NOT EXISTS ix_sources_status ON sources (status)",
            "CREATE INDEX IF NOT EXISTS ix_sources_created_at ON sources (created_at)",
            "CREATE INDEX IF NOT EXISTS ix_messages_role ON messages (role)",
            "CREATE INDEX IF NOT EXISTS ix_artifacts_kind ON artifacts (kind)",
        ):
            try:
                conn.exec_driver_sql(index_sql)
            except Exception as exc:
                logger.debug("Index creation skipped: %s", exc)
        # Belt and suspenders: make sure no legacy row is left without a timestamp.
        if columns and "updated_at" in {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(sources)").fetchall()}:
            conn.execute(
                text("UPDATE sources SET updated_at = created_at WHERE updated_at IS NULL"),
            )


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
        try:
            yield session
        except Exception:
            session.rollback()
            raise
