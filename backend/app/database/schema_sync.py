"""Additive schema reconciliation for development databases.

``Base.metadata.create_all()`` creates missing *tables* but never alters an
existing one, so adding a column to a model silently leaves the live database
behind and every query against that column fails at runtime.

Alembic is the correct tool for production schema evolution and is configured in
``backend/alembic/``. This module is a narrow, deliberately limited safety net
for local and demo databases that predate a model change:

* It creates missing tables.
* It adds missing **nullable** columns via ``ALTER TABLE ... ADD COLUMN``.
* It creates missing indexes.

It will never drop a table, drop a column, change a column type, or alter a
constraint, because none of those are safe to infer automatically. Anything it
cannot handle is reported so the operator can write a real migration.

Controlled by ``AUTO_SCHEMA_SYNC`` (default: enabled outside production).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Set

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.schema import CreateIndex

from app.core.config import settings
from app.database.db import Base, engine as default_engine
import app.models  # noqa: F401  # register models on Base.metadata

logger = logging.getLogger(__name__)


@dataclass
class SchemaSyncReport:
    """What the reconciler did, and what it refused to do."""

    created_tables: List[str] = field(default_factory=list)
    added_columns: List[str] = field(default_factory=list)
    created_indexes: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.created_tables or self.added_columns or self.created_indexes)

    def as_dict(self) -> Dict[str, object]:
        return {
            "created_tables": self.created_tables,
            "added_columns": self.added_columns,
            "created_indexes": self.created_indexes,
            "skipped": self.skipped,
            "errors": self.errors,
            "changed": self.changed,
        }

    def log(self) -> None:
        if self.created_tables:
            logger.info("schema-sync created tables: %s", ", ".join(self.created_tables))
        if self.added_columns:
            logger.info("schema-sync added columns: %s", ", ".join(self.added_columns))
        if self.created_indexes:
            logger.info("schema-sync created indexes: %s", ", ".join(self.created_indexes))
        for item in self.skipped:
            logger.warning("schema-sync skipped (needs a real migration): %s", item)
        for item in self.errors:
            logger.error("schema-sync error: %s", item)


def _render_column_type(column, dialect) -> str:
    return column.type.compile(dialect=dialect)


def _render_default(column, dialect) -> str:
    """Render a literal DEFAULT clause for simple scalar Python defaults."""
    default = column.default
    if default is None or not getattr(default, "is_scalar", False):
        return ""
    value = default.arg
    if isinstance(value, bool):
        return f" DEFAULT {1 if value else 0}"
    if isinstance(value, (int, float)):
        return f" DEFAULT {value}"
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f" DEFAULT '{escaped}'"
    return ""


def sync_schema(engine: Engine | None = None, dry_run: bool = False) -> SchemaSyncReport:
    """Bring the live schema up to date with the models, additively."""
    engine = engine or default_engine
    report = SchemaSyncReport()
    dialect = engine.dialect

    inspector = inspect(engine)
    existing_tables: Set[str] = set(inspector.get_table_names())

    # 1. Missing tables: create_all handles these correctly and safely.
    missing_tables = [
        table for name, table in Base.metadata.tables.items() if name not in existing_tables
    ]
    if missing_tables and not dry_run:
        try:
            Base.metadata.create_all(bind=engine, tables=missing_tables)
            report.created_tables = sorted(t.name for t in missing_tables)
        except SQLAlchemyError as exc:
            report.errors.append(f"creating tables: {exc}")
    elif missing_tables:
        report.created_tables = sorted(t.name for t in missing_tables)

    # Refresh after creating tables.
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    # 2. Missing columns on tables that already exist.
    for table_name, table in Base.metadata.tables.items():
        if table_name not in existing_tables:
            continue

        live_columns = {col["name"] for col in inspector.get_columns(table_name)}

        for column in table.columns:
            if column.name in live_columns:
                continue

            # Refuse anything that cannot be added safely to a populated table.
            if not column.nullable and column.default is None and column.server_default is None:
                report.skipped.append(
                    f"{table_name}.{column.name} is NOT NULL without a default; "
                    "write an Alembic migration that backfills it"
                )
                continue
            if column.primary_key:
                report.skipped.append(
                    f"{table_name}.{column.name} is part of the primary key; "
                    "cannot be added in place"
                )
                continue

            col_type = _render_column_type(column, dialect)
            default_clause = _render_default(column, dialect)
            null_clause = "" if column.nullable else " NOT NULL"
            ddl = (
                f"ALTER TABLE {table_name} "
                f"ADD COLUMN {column.name} {col_type}{default_clause}{null_clause}"
            )

            report.added_columns.append(f"{table_name}.{column.name}")
            if dry_run:
                continue

            try:
                with engine.begin() as connection:
                    connection.execute(text(ddl))
            except SQLAlchemyError as exc:
                report.added_columns.pop()
                report.errors.append(f"{table_name}.{column.name}: {exc}")

    # 3. Missing indexes.
    inspector = inspect(engine)
    for table_name, table in Base.metadata.tables.items():
        if table_name not in set(inspector.get_table_names()):
            continue
        try:
            live_indexes = {idx["name"] for idx in inspector.get_indexes(table_name)}
        except SQLAlchemyError:  # pragma: no cover - dialect dependent
            continue

        live_columns = {col["name"] for col in inspector.get_columns(table_name)}

        for index in table.indexes:
            if index.name in live_indexes:
                continue
            # Only create an index whose columns all exist.
            index_columns = {col.name for col in index.columns}
            if not index_columns.issubset(live_columns):
                report.skipped.append(
                    f"index {index.name} references columns not present in {table_name}"
                )
                continue

            report.created_indexes.append(str(index.name))
            if dry_run:
                continue
            try:
                with engine.begin() as connection:
                    connection.execute(CreateIndex(index))
            except SQLAlchemyError as exc:
                report.created_indexes.pop()
                report.errors.append(f"index {index.name}: {exc}")

    return report


def auto_sync_if_enabled(engine: Engine | None = None) -> SchemaSyncReport | None:
    """Run the reconciler when configuration allows it.

    Defaults to **SQLite only**. On a server database Alembic owns the schema,
    and having two mechanisms able to issue DDL invites drift between what a
    migration says the schema is and what it actually is. Set
    ``AUTO_SCHEMA_SYNC=true`` to force it on anyway, or ``false`` to disable it
    for SQLite too.
    """
    enabled_default = settings.is_sqlite and not settings.is_production
    from app.core.config import _env_bool  # local import: internal helper

    if not _env_bool("AUTO_SCHEMA_SYNC", enabled_default):
        logger.debug(
            "Additive schema reconciliation skipped (dialect=%s). "
            "Schema changes are applied with 'alembic upgrade head'.",
            "sqlite" if settings.is_sqlite else "server",
        )
        return None

    report = sync_schema(engine)
    report.log()
    return report


__all__ = ["SchemaSyncReport", "auto_sync_if_enabled", "sync_schema"]
