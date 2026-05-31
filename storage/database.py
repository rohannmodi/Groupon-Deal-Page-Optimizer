"""
DuckDB connection manager.

Single connection, context-manager safe. All writes are transactional.
Call `get_db()` anywhere in the codebase to get the active connection.

Schema changes are managed via numbered SQL files in storage/migrations/.
The migration runner in storage/migrate.py is called on first connection
and applies any pending migrations automatically. To add a column or table
next sprint, create storage/migrations/XXXX_description.sql — no code
changes required.
"""

from __future__ import annotations

import threading
from typing import Optional

import duckdb

from config.settings import settings


_lock = threading.Lock()
_connection: Optional[duckdb.DuckDBPyConnection] = None


def get_db() -> duckdb.DuckDBPyConnection:
    """
    Return the singleton DuckDB connection.

    On first call: creates the database file, runs all pending migrations
    (see storage/migrate.py), and caches the connection for the process.
    """
    global _connection
    if _connection is None:
        with _lock:
            if _connection is None:  # double-checked locking
                settings.ensure_dirs()
                _connection = duckdb.connect(str(settings.db_path))
                # Import here to avoid circular imports at module load time
                from storage.migrate import run_migrations
                run_migrations(_connection)
    return _connection


def close_db() -> None:
    """Cleanly close the connection (call at process exit)."""
    global _connection
    if _connection is not None:
        _connection.close()
        _connection = None
