"""
Schema migration runner.

Migrations live in storage/migrations/ as numbered SQL files:
    0001_initial_schema.sql
    0002_add_score_reasoning.sql
    ...

On startup, the runner:
  1. Creates the schema_migrations table if absent.
  2. Reads every *.sql file in migrations/, sorted by version number.
  3. Skips files whose version is already recorded in schema_migrations.
  4. Applies the rest in order, each in its own transaction.

Existing databases (pre-migration-runner) are handled by the "baseline"
mechanic: if the deals table already exists but schema_migrations is empty,
migration 0001 (which uses CREATE TABLE IF NOT EXISTS) is safe to re-apply
and will be marked as applied without error.

Adding a column next sprint:
    1. Create storage/migrations/0003_my_change.sql  (ALTER TABLE … ADD COLUMN)
    2. That's it — the runner picks it up on next startup automatically.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# Matches filenames like 0001_initial_schema.sql
_MIGRATION_RE = re.compile(r"^(\d{4})_(.+)\.sql$")


def run_migrations(conn: "duckdb.DuckDBPyConnection") -> None:
    """
    Apply all pending migrations to `conn`.
    Called once per process by database.py on first connection.
    """
    _ensure_migrations_table(conn)
    applied = _get_applied_versions(conn)

    pending = _collect_pending(applied)
    if not pending:
        log.debug("Schema is up to date (%d migration(s) applied)", len(applied))
        return

    for version, name, path in pending:
        _apply_migration(conn, version, name, path)

    log.info(
        "Applied %d migration(s). Schema version: %04d",
        len(pending),
        pending[-1][0],
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ensure_migrations_table(conn: "duckdb.DuckDBPyConnection") -> None:
    """Create the schema_migrations tracking table if it doesn't exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version     INTEGER PRIMARY KEY,
            name        TEXT    NOT NULL,
            applied_at  TIMESTAMP DEFAULT current_timestamp
        )
        """
    )


def _get_applied_versions(conn: "duckdb.DuckDBPyConnection") -> set[int]:
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {r[0] for r in rows}


def _collect_pending(
    applied: set[int],
) -> list[tuple[int, str, Path]]:
    """
    Scan migrations/ and return (version, name, path) tuples for every
    migration not yet in `applied`, sorted ascending.
    """
    pending: list[tuple[int, str, Path]] = []

    if not MIGRATIONS_DIR.exists():
        log.warning("Migrations directory not found: %s", MIGRATIONS_DIR)
        return pending

    for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
        m = _MIGRATION_RE.match(f.name)
        if not m:
            log.warning("Skipping unrecognised migration file: %s", f.name)
            continue
        version = int(m.group(1))
        name = m.group(2)
        if version not in applied:
            pending.append((version, name, f))

    return pending


def _apply_migration(
    conn: "duckdb.DuckDBPyConnection",
    version: int,
    name: str,
    path: Path,
) -> None:
    """Run a single migration file and record it in schema_migrations."""
    sql = path.read_text(encoding="utf-8")
    log.info("Applying migration %04d — %s", version, name)

    # Execute each statement individually (DuckDB Python API is single-statement)
    for raw in sql.split(";"):
        stmt = raw.strip()
        lines = [
            ln for ln in stmt.splitlines()
            if ln.strip() and not ln.strip().startswith("--")
        ]
        if not lines:
            continue
        try:
            conn.execute(stmt)
        except Exception as exc:
            raise RuntimeError(
                f"Migration {version:04d} ({name}) failed on statement:\n"
                f"{stmt}\n\nOriginal error: {exc}"
            ) from exc

    conn.execute(
        "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
        [version, name],
    )
    log.info("Migration %04d applied successfully", version)


# ---------------------------------------------------------------------------
# CLI helper  (python -m storage.migrate)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from storage.database import get_db  # noqa: E402 (local import for CLI only)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    db = get_db()

    applied = _get_applied_versions(db)
    pending = _collect_pending(applied)

    if "--status" in sys.argv:
        print(f"Applied migrations: {sorted(applied) or 'none'}")
        print(f"Pending migrations: {[v for v, _, _ in pending] or 'none'}")
        sys.exit(0)

    run_migrations(db)
    print("Done.")
