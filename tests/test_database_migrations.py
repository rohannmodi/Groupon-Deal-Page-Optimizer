"""
Tests for storage/migrate.py

Covers the migration runner against an in-memory DuckDB database.
No files on disk, fast, fully isolated.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import duckdb
import pytest

from storage.migrate import (
    _collect_pending,
    _ensure_migrations_table,
    _get_applied_versions,
    run_migrations,
)


@pytest.fixture()
def fresh_conn():
    conn = duckdb.connect(":memory:")
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# _ensure_migrations_table
# ---------------------------------------------------------------------------

class TestEnsureMigrationsTable:
    def test_creates_table(self, fresh_conn):
        _ensure_migrations_table(fresh_conn)
        # Table should now exist and be queryable
        rows = fresh_conn.execute("SELECT version FROM schema_migrations").fetchall()
        assert rows == []

    def test_idempotent(self, fresh_conn):
        # Running twice should not raise
        _ensure_migrations_table(fresh_conn)
        _ensure_migrations_table(fresh_conn)


# ---------------------------------------------------------------------------
# _get_applied_versions
# ---------------------------------------------------------------------------

class TestGetAppliedVersions:
    def test_empty_on_fresh_db(self, fresh_conn):
        _ensure_migrations_table(fresh_conn)
        assert _get_applied_versions(fresh_conn) == set()

    def test_returns_applied_versions(self, fresh_conn):
        _ensure_migrations_table(fresh_conn)
        fresh_conn.execute(
            "INSERT INTO schema_migrations (version, name) VALUES (1, 'initial')"
        )
        fresh_conn.execute(
            "INSERT INTO schema_migrations (version, name) VALUES (2, 'add_column')"
        )
        assert _get_applied_versions(fresh_conn) == {1, 2}


# ---------------------------------------------------------------------------
# _collect_pending
# ---------------------------------------------------------------------------

class TestCollectPending:
    def test_returns_pending_only(self, tmp_path: Path):
        (tmp_path / "0001_schema.sql").write_text("-- migration 1")
        (tmp_path / "0002_alter.sql").write_text("-- migration 2")
        (tmp_path / "0003_new.sql").write_text("-- migration 3")

        # Simulate 0001 and 0002 already applied
        import storage.migrate as m
        original_dir = m.MIGRATIONS_DIR
        m.MIGRATIONS_DIR = tmp_path
        try:
            pending = _collect_pending(applied={1, 2})
            assert len(pending) == 1
            assert pending[0][0] == 3
        finally:
            m.MIGRATIONS_DIR = original_dir

    def test_sorted_by_version(self, tmp_path: Path):
        # Create out of order to test sorting
        (tmp_path / "0003_c.sql").write_text("-- c")
        (tmp_path / "0001_a.sql").write_text("-- a")
        (tmp_path / "0002_b.sql").write_text("-- b")

        import storage.migrate as m
        original_dir = m.MIGRATIONS_DIR
        m.MIGRATIONS_DIR = tmp_path
        try:
            pending = _collect_pending(applied=set())
            versions = [p[0] for p in pending]
            assert versions == [1, 2, 3]
        finally:
            m.MIGRATIONS_DIR = original_dir

    def test_ignores_non_sql_files(self, tmp_path: Path):
        (tmp_path / "0001_schema.sql").write_text("-- migration")
        (tmp_path / "README.md").write_text("# readme")
        (tmp_path / "notes.txt").write_text("notes")

        import storage.migrate as m
        original_dir = m.MIGRATIONS_DIR
        m.MIGRATIONS_DIR = tmp_path
        try:
            pending = _collect_pending(applied=set())
            assert len(pending) == 1
        finally:
            m.MIGRATIONS_DIR = original_dir

    def test_missing_directory_returns_empty(self):
        import storage.migrate as m
        original_dir = m.MIGRATIONS_DIR
        m.MIGRATIONS_DIR = Path("/nonexistent/path/migrations")
        try:
            pending = _collect_pending(applied=set())
            assert pending == []
        finally:
            m.MIGRATIONS_DIR = original_dir


# ---------------------------------------------------------------------------
# run_migrations — integration
# ---------------------------------------------------------------------------

class TestRunMigrations:
    def test_applies_pending_migrations(self, fresh_conn, tmp_path: Path):
        # Write a simple valid migration
        (tmp_path / "0001_create_test.sql").write_text(
            "CREATE TABLE test_table (id INTEGER PRIMARY KEY, name TEXT)"
        )

        import storage.migrate as m
        original_dir = m.MIGRATIONS_DIR
        m.MIGRATIONS_DIR = tmp_path
        try:
            run_migrations(fresh_conn)
            # Table should exist
            fresh_conn.execute("SELECT * FROM test_table").fetchall()
            # Migration should be recorded
            applied = _get_applied_versions(fresh_conn)
            assert 1 in applied
        finally:
            m.MIGRATIONS_DIR = original_dir

    def test_idempotent_on_rerun(self, fresh_conn, tmp_path: Path):
        (tmp_path / "0001_create_test.sql").write_text(
            "CREATE TABLE IF NOT EXISTS test_table (id INTEGER PRIMARY KEY)"
        )

        import storage.migrate as m
        original_dir = m.MIGRATIONS_DIR
        m.MIGRATIONS_DIR = tmp_path
        try:
            run_migrations(fresh_conn)  # first run
            run_migrations(fresh_conn)  # second run — should not raise or re-apply
            applied = _get_applied_versions(fresh_conn)
            assert len(applied) == 1  # still just 1, not 2
        finally:
            m.MIGRATIONS_DIR = original_dir

    def test_applies_real_project_migrations(self, in_memory_db):
        """
        Smoke test: the real project migrations apply cleanly to a fresh DB.
        Verifies that the deals table (migration 0001) exists.
        """
        # in_memory_db fixture already ran run_migrations — just check the result.
        # Use SHOW TABLES (DuckDB-native) rather than information_schema.tables
        # because the catalog name for :memory: connections is 'memory', not 'main',
        # which makes table_schema filters on information_schema unreliable.
        tables = {row[0] for row in in_memory_db.execute("SHOW TABLES").fetchall()}
        assert "deals" in tables
        assert "pipeline_runs" in tables
        assert "audit_scores" in tables
        assert "optimization_proposals" in tables

    def test_failed_migration_raises_runtime_error(self, fresh_conn, tmp_path: Path):
        (tmp_path / "0001_bad.sql").write_text("THIS IS NOT VALID SQL !!!!")

        import storage.migrate as m
        original_dir = m.MIGRATIONS_DIR
        m.MIGRATIONS_DIR = tmp_path
        try:
            with pytest.raises(RuntimeError, match="Migration 0001"):
                run_migrations(fresh_conn)
        finally:
            m.MIGRATIONS_DIR = original_dir
