"""Database engine, session factory and session lifecycle helpers.

Changes from the original 28-line version:

* Configuration comes from ``app.core.config`` instead of an inline ``os.getenv``.
* Connection pooling is configured for server databases (PostgreSQL/MySQL);
  pool arguments are omitted for SQLite, whose pool does not accept them.
* SQLite gets WAL journalling and enforced foreign keys via a connect hook, so
  concurrent readers are not blocked by a writer and FK constraints actually
  apply (SQLite ignores them by default).
* ``get_db`` now rolls back on exception. Previously a failing handler left the
  unit of work to be silently discarded by ``close()``, which on a partially
  flushed session could leave the session in an undefined state.
* ``session_scope`` provides the same guarantees for non-request callers such as
  the scheduler and CLI scripts, replacing the ``next(get_db())`` pattern.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Dict, Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.core.config import settings

logger = logging.getLogger(__name__)

DATABASE_URL = settings.database_url

_engine_kwargs: Dict[str, Any] = {"echo": settings.sql_echo, "future": True}

if settings.is_sqlite:
    # check_same_thread=False is required because FastAPI runs sync endpoints in
    # a threadpool, so a session may be touched from a different thread than the
    # one that created it.
    _engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
else:
    _engine_kwargs.update(
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=settings.db_pool_recycle_seconds,
        # Verifies a pooled connection is still alive before handing it out,
        # which avoids "server closed the connection unexpectedly" after idle.
        pool_pre_ping=True,
    )

engine: Engine = create_engine(DATABASE_URL, **_engine_kwargs)


if settings.is_sqlite:

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, connection_record):  # pragma: no cover
        """Apply per-connection SQLite pragmas.

        WAL lets readers proceed during a write, which matters because a
        reconciliation run holds a write transaction for its whole duration.
        """
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=NORMAL")
            # Wait rather than immediately raising "database is locked".
            cursor.execute("PRAGMA busy_timeout=30000")
        finally:
            cursor.close()


SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    expire_on_commit=False,
)

Base = declarative_base()


def get_db() -> Iterator[Session]:
    """FastAPI request-scoped session dependency.

    Rolls back on exception so a failed request can never leave partially
    flushed state behind, then always closes the connection.
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session scope for code outside the request lifecycle.

    Commits on success, rolls back on failure. Use this from the scheduler,
    background workers and scripts instead of driving ``get_db`` manually.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Rolling back session after unhandled error")
        raise
    finally:
        db.close()


__all__ = [
    "DATABASE_URL",
    "Base",
    "SessionLocal",
    "engine",
    "get_db",
    "session_scope",
]
