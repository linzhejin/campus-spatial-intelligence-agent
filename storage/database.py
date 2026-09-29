"""Small PostgreSQL boundary for durable agent state.

No in-memory or JSON fallback is provided: callers must surface storage
unavailability instead of treating failed persistence as an empty session.
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import psycopg
from psycopg.rows import dict_row

_MIGRATIONS = Path(__file__).with_name("migrations")
_MIGRATION_LOCK = threading.Lock()
_INITIALIZED_URLS: set[str] = set()


def database_url(url: str | None = None) -> str:
    value = url or os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is required for durable agent state")
    return value


@contextmanager
def connect(url: str | None = None) -> Iterator[psycopg.Connection]:
    with psycopg.connect(database_url(url), row_factory=dict_row, connect_timeout=5) as conn:
        yield conn


def initialize(url: str | None = None) -> None:
    """Apply versioned SQL migrations atomically and idempotently."""
    resolved_url = database_url(url)
    if resolved_url in _INITIALIZED_URLS:
        return
    migration_files = sorted(_MIGRATIONS.glob("*.sql"))
    with _MIGRATION_LOCK:
        if resolved_url in _INITIALIZED_URLS:
            return
        with connect(resolved_url) as conn:
            # Serialize workers/processes starting against the same fresh schema.
            conn.execute("SELECT pg_advisory_xact_lock(735182640119)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS agent_schema_migration "
                "(version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            applied = {row["version"] for row in conn.execute(
                "SELECT version FROM agent_schema_migration"
            ).fetchall()}
            for migration in migration_files:
                version = migration.name
                if version in applied:
                    continue
                conn.execute(migration.read_text(encoding="utf-8"))
                conn.execute(
                    "INSERT INTO agent_schema_migration(version) VALUES (%s)", (version,)
                )
        _INITIALIZED_URLS.add(resolved_url)
