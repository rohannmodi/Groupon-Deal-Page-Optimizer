"""
Shared pytest fixtures.

Key design decisions:
  - All DuckDB tests use an in-memory database (:memory:) so they
    are fast, isolated, and leave no files on disk.
  - Fixtures that need the real project root add it to sys.path so
    absolute imports (from models import ...) work without installing
    the package.
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pytest

# Make the project root importable from tests/
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def in_memory_db() -> duckdb.DuckDBPyConnection:
    """
    A fresh in-memory DuckDB connection with all migrations applied.
    Each test gets its own isolated database.
    """
    conn = duckdb.connect(":memory:")
    from storage.migrate import run_migrations
    run_migrations(conn)
    yield conn
    conn.close()


@pytest.fixture()
def minimal_deal_audit():
    """A DealAudit with just enough data to pass to AI modules."""
    from models import DealAudit, PricingOption, SEOElements

    return DealAudit(
        deal_id="test-deal-abc123",
        url="https://www.groupon.com/deals/test-deal",
        title="1-Hour Swedish Massage at Serenity Spa",
        merchant_name="Serenity Spa",
        category="Spa & Massage",
        city="Chicago",
        state="IL",
        pricing_options=[
            PricingOption(
                option_name="60-Minute Swedish Massage",
                original_price=110.00,
                deal_price=49.00,
                discount_pct=55.0,
                savings_amount=61.00,
                is_primary=True,
            )
        ],
        highlights=["60-minute Swedish massage", "Licensed therapist", "Free parking"],
        fine_print=["Appointment required", "New customers only"],
        seo=SEOElements(
            meta_title="Swedish Massage at Serenity Spa | Groupon",
            meta_description="60-minute Swedish massage for $49. Book online.",
            h1_text="1-Hour Swedish Massage at Serenity Spa",
        ),
        reviews_count=142,
        reviews_avg_rating=4.8,
    )
