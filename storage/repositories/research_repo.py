"""
Research repository — all DB reads/writes for Stage 2 data.

Covers:
  - Competitor prices
  - Merchant reviews (Yelp, Google)
  - Review themes (AI-coded)
  - Research sources (citations)
  - Research synthesis (AI output)
  - Audit scores (Stage 1 AI output)

All writes are idempotent: delete + re-insert on deal_id for 1:1 tables,
delete by deal_id for 1:many tables.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from models import AuditScores, ResearchData, ResearchSynthesis
from storage.database import get_db


# ---------------------------------------------------------------------------
# Audit scores (Stage 1 AI)
# ---------------------------------------------------------------------------

def upsert_audit_scores(deal_id: str, scores: AuditScores, usage_prompt: int, usage_completion: int) -> None:
    db = get_db()
    db.execute("DELETE FROM audit_scores WHERE deal_id = ?", [deal_id])
    db.execute(
        """
        INSERT INTO audit_scores (
            deal_id, clarity_score, trust_score, urgency_score, seo_score,
            value_comm_score, completeness_score, overall_score,
            content_gaps, ai_flags, analyzed_at, prompt_tokens, completion_tokens
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            deal_id,
            scores.clarity_score,
            scores.trust_score,
            scores.urgency_score,
            scores.seo_score,
            scores.value_comm_score,
            scores.completeness_score,
            scores.overall_score,
            json.dumps(scores.content_gaps),
            json.dumps(scores.ai_flags),
            datetime.utcnow(),
            usage_prompt,
            usage_completion,
        ],
    )


def get_audit_scores(deal_id: str) -> Optional[dict]:
    db = get_db()
    row = db.execute(
        "SELECT * FROM audit_scores WHERE deal_id = ?", [deal_id]
    ).fetchone()
    if row is None:
        return None
    cols = [d[0] for d in db.description]
    return dict(zip(cols, row))


# ---------------------------------------------------------------------------
# Research data (raw stage 2 data)
# ---------------------------------------------------------------------------

def upsert_research_data(data: ResearchData) -> None:
    """Store all raw research data for a deal."""
    _delete_research_children(data.deal_id)

    db = get_db()

    # Competitor prices
    for comp in data.competitors:
        db.execute(
            """
            INSERT INTO competitor_prices
                (deal_id, competitor_name, service_name, regular_price,
                 sale_price, source_url, scraped_at, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                data.deal_id,
                comp.competitor_name,
                comp.service_name,
                comp.regular_price,
                comp.sale_price,
                comp.source_url,
                datetime.utcnow(),
                comp.notes,
            ],
        )

    # Yelp merchant review
    if data.yelp:
        db.execute(
            """
            INSERT INTO merchant_reviews
                (deal_id, platform, overall_rating, review_count, scraped_at)
            VALUES (?, 'yelp', ?, ?, ?)
            """,
            [data.deal_id, data.yelp.rating, data.yelp.review_count, datetime.utcnow()],
        )

    # Google merchant review
    if data.google:
        db.execute(
            """
            INSERT INTO merchant_reviews
                (deal_id, platform, overall_rating, review_count, scraped_at)
            VALUES (?, 'google', ?, ?, ?)
            """,
            [data.deal_id, data.google.rating, data.google.review_count, datetime.utcnow()],
        )

    # Research sources
    for src in data.sources:
        db.execute(
            """
            INSERT INTO research_sources
                (deal_id, source_type, source_url, source_title, fetched_at, relevance)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                data.deal_id,
                src.source_type,
                src.source_url,
                src.source_title,
                src.fetched_at,
                src.relevance,
            ],
        )


def upsert_research_synthesis(deal_id: str, synthesis: ResearchSynthesis) -> None:
    """Store the AI-generated research synthesis."""
    db = get_db()
    db.execute("DELETE FROM research_synthesis WHERE deal_id = ?", [deal_id])
    db.execute(
        """
        INSERT INTO research_synthesis (
            deal_id, value_assessment, groupon_vs_direct_savings,
            groupon_vs_competitor_savings, merchant_differentiators,
            red_flags, deal_quality, synthesized_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            deal_id,
            synthesis.value_assessment,
            synthesis.groupon_vs_direct_savings,
            synthesis.groupon_vs_competitor_savings,
            json.dumps(synthesis.merchant_differentiators),
            json.dumps(synthesis.red_flags),
            synthesis.deal_quality,
            datetime.utcnow(),
        ],
    )

    # Review themes (1:many)
    db.execute("DELETE FROM review_themes WHERE deal_id = ?", [deal_id])
    for theme in synthesis.review_themes:
        db.execute(
            """
            INSERT INTO review_themes
                (deal_id, platform, theme, sentiment, mention_count, sample_quote)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                deal_id,
                theme.platform,
                theme.theme,
                theme.sentiment,
                theme.mention_count,
                theme.sample_quote,
            ],
        )


def get_research_synthesis(deal_id: str) -> Optional[dict]:
    db = get_db()
    row = db.execute(
        "SELECT * FROM research_synthesis WHERE deal_id = ?", [deal_id]
    ).fetchone()
    if row is None:
        return None
    cols = [d[0] for d in db.description]
    return dict(zip(cols, row))


def get_review_themes(deal_id: str) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM review_themes WHERE deal_id = ? ORDER BY mention_count DESC",
        [deal_id],
    ).fetchall()
    cols = [d[0] for d in db.description]
    return [dict(zip(cols, r)) for r in rows]


def get_competitor_prices(deal_id: str) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM competitor_prices WHERE deal_id = ?", [deal_id]
    ).fetchall()
    cols = [d[0] for d in db.description]
    return [dict(zip(cols, r)) for r in rows]


def _delete_research_children(deal_id: str) -> None:
    db = get_db()
    for table in ("competitor_prices", "merchant_reviews", "research_sources"):
        db.execute(f"DELETE FROM {table} WHERE deal_id = ?", [deal_id])
