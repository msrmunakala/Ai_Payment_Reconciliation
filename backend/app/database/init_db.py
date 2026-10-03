"""Schema bootstrap.

``create_all`` only creates *missing* tables; it never alters an existing one.
Alembic is the mechanism for evolving columns (see ``backend/alembic/``). This
helper remains for first-run convenience and for tests.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect

from app.database.db import Base, engine
import app.models  # noqa: F401  # registers every model on Base.metadata

logger = logging.getLogger(__name__)


def init_db() -> None:
    """Create any tables that do not exist yet. Idempotent."""
    before = set(inspect(engine).get_table_names())
    Base.metadata.create_all(bind=engine)
    after = set(inspect(engine).get_table_names())

    created = sorted(after - before)
    if created:
        logger.info("Created database tables: %s", ", ".join(created))


def drop_db() -> None:
    """Drop every known table. Intended for tests only."""
    Base.metadata.drop_all(bind=engine)


__all__ = ["init_db", "drop_db"]
