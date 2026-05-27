"""
Deal repository — all DB reads/writes for the scrape/parse stage.

The orchestrator only calls these methods; it never writes raw SQL.
All writes use INSERT OR REPLACE (upsert) so re-runs are idempotent.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from models import DealAudit, DealInput, PipelineRun
from storage.database import get_db


# ---------------------------------------------------------------------------
# Deals table
# ---------------------------------------------------------------------------

def upsert_deal(audit: DealAudit) -> None:
    """Write (or overwrite) the core deal row and all child rows."""
    db = get_db()

    # --- deals ---
    db.execute(
        """
        INSERT OR REPLACE INTO deals (
            deal_id, url, title, subtitle, merchant_name, category,
            city, state, description, reviews_count, reviews_avg_rating,
            scraped_at, scrape_duration_s, raw_html_path, scrape_error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            audit.deal_id,
            audit.url,
            audit.title,
            audit.subtitle,
            audit.merchant_name,
            audit.category,
            audit.city,
            audit.state,
            audit.description,
            audit.reviews_count,
            audit.reviews_avg_rating,
            audit.scraped_at,
            audit.scrape_duration_seconds,
            audit.raw_html_path,
            audit.scrape_error,
        ],
    )

    # --- child tables: delete then re-insert (simpler than row-level upsert) ---
    _delete_children(audit.deal_id)

    # pricing_options
    for opt in audit.pricing_options:
        db.execute(
            """
            INSERT INTO pricing_options
                (deal_id, option_name, original_price, deal_price,
                 discount_pct, savings_amount, is_primary)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                audit.deal_id,
                opt.option_name,
                opt.original_price,
                opt.deal_price,
                opt.discount_pct,
                opt.savings_amount,
                opt.is_primary,
            ],
        )

    # deal_content
    db.execute(
        """
        INSERT OR REPLACE INTO deal_content (deal_id, highlights, fine_print, faqs)
        VALUES (?, ?, ?, ?)
        """,
        [
            audit.deal_id,
            json.dumps(audit.highlights),
            json.dumps(audit.fine_print),
            json.dumps([f.model_dump() for f in audit.faqs]),
        ],
    )

    # deal_images
    for img in audit.images:
        db.execute(
            """
            INSERT INTO deal_images
                (deal_id, src_url, alt_text, estimated_width, image_type, position)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                audit.deal_id,
                img.src_url,
                img.alt_text,
                img.estimated_width,
                img.image_type,
                img.position,
            ],
        )

    # seo_elements
    seo = audit.seo
    db.execute(
        """
        INSERT OR REPLACE INTO seo_elements (
            deal_id, meta_title, meta_description, h1_text, h2_texts,
            has_schema_markup, schema_type, canonical_url,
            og_title, og_description, og_image_url, image_count, script_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            audit.deal_id,
            seo.meta_title,
            seo.meta_description,
            seo.h1_text,
            json.dumps(seo.h2_texts),
            seo.has_schema_markup,
            seo.schema_type,
            seo.canonical_url,
            seo.og_title,
            seo.og_description,
            seo.og_image_url,
            seo.image_count,
            seo.script_count,
        ],
    )

    # trust_signals
    for ts in audit.trust_signals:
        db.execute(
            "INSERT INTO trust_signals (deal_id, signal_type, signal_value, impact_score) VALUES (?, ?, ?, ?)",
            [audit.deal_id, ts.signal_type, ts.signal_value, ts.impact_score],
        )

    # urgency_elements
    for ue in audit.urgency_elements:
        db.execute(
            "INSERT INTO urgency_elements (deal_id, element_type, element_text, is_visible_atf) VALUES (?, ?, ?, ?)",
            [audit.deal_id, ue.element_type, ue.element_text, ue.is_visible_atf],
        )

    # review_samples
    for rs in audit.review_samples:
        db.execute(
            "INSERT INTO review_samples (deal_id, author, rating, review_text, review_date) VALUES (?, ?, ?, ?, ?)",
            [audit.deal_id, rs.author, rs.rating, rs.text, rs.date],
        )


def get_deal(deal_id: str) -> Optional[dict]:
    """Return the raw deals row as a dict, or None if not found."""
    db = get_db()
    row = db.execute("SELECT * FROM deals WHERE deal_id = ?", [deal_id]).fetchone()
    if row is None:
        return None
    cols = [d[0] for d in db.description]
    return dict(zip(cols, row))


def _delete_children(deal_id: str) -> None:
    """Remove child rows before re-inserting (makes upsert simple)."""
    db = get_db()
    for table in (
        "pricing_options",
        "deal_content",
        "deal_images",
        "seo_elements",
        "trust_signals",
        "urgency_elements",
        "review_samples",
    ):
        db.execute(f"DELETE FROM {table} WHERE deal_id = ?", [deal_id])


# ---------------------------------------------------------------------------
# Pipeline runs
# ---------------------------------------------------------------------------

def upsert_pipeline_run(run: PipelineRun) -> None:
    """Record a stage's status. Used by checkpoint and orchestrator."""
    db = get_db()

    # Ensure the deal row exists (with minimal data) so FK constraint holds
    db.execute(
        "INSERT OR IGNORE INTO deals (deal_id, url) VALUES (?, ?)",
        [run.deal_id, ""],
    )

    db.execute(
        """
        INSERT INTO pipeline_runs
            (deal_id, stage, status, started_at, completed_at, error_message, retry_count)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            run.deal_id,
            run.stage,
            run.status,
            run.started_at,
            run.completed_at,
            run.error_message,
            run.retry_count,
        ],
    )


def get_stage_status(deal_id: str, stage: str) -> Optional[str]:
    """Return the most recent status for a (deal, stage) pair, or None."""
    db = get_db()
    row = db.execute(
        """
        SELECT status FROM pipeline_runs
        WHERE deal_id = ? AND stage = ?
        ORDER BY id DESC LIMIT 1
        """,
        [deal_id, stage],
    ).fetchone()
    return row[0] if row else None


def get_all_deals() -> list[dict]:
    """Return all deal rows (for reporting / status display)."""
    db = get_db()
    rows = db.execute("SELECT * FROM deals ORDER BY scraped_at DESC").fetchall()
    cols = [d[0] for d in db.description]
    return [dict(zip(cols, r)) for r in rows]
