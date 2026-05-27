"""
DuckDB connection manager.

Single connection, context-manager safe. All writes are transactional.
Call `get_db()` anywhere in the codebase to get the active connection.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

import duckdb

from config.settings import settings


_lock = threading.Lock()
_connection: Optional[duckdb.DuckDBPyConnection] = None


def get_db() -> duckdb.DuckDBPyConnection:
    """
    Return the singleton DuckDB connection, initialising the schema
    on first call.
    """
    global _connection
    if _connection is None:
        with _lock:
            if _connection is None:  # double-checked locking
                settings.ensure_dirs()
                _connection = duckdb.connect(str(settings.db_path))
                _apply_schema(_connection)
    return _connection


def _apply_schema(conn: duckdb.DuckDBPyConnection) -> None:
    schema_path = Path(__file__).parent / "schema.sql"
    sql = schema_path.read_text()
    # DuckDB can execute a multi-statement string directly
    conn.execute(sql)


def close_db() -> None:
    """Cleanly close the connection (call at process exit)."""
    global _connection
    if _connection is not None:
        _connection.close()
        _connection = None
