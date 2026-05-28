"""
Proposal repository — DB reads/writes for Stage 3 data.

Covers:
  - optimization_proposals (1:1 with deal)
  - proposal_recommendations (1:many)

All writes are idempotent: delete + re-insert on deal_id.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from models import OptimizationProposal
from storage.database import get_db


def upsert_proposal(proposal: OptimizationProposal, usage_prompt: int, usage_completion: int) -> None:
    """Persist a complete proposal + its recommendations to DuckDB."""
    db = get_db()

    # ── optimization_proposals (1:1) ──────────────────────────────────────────
    db.execute("DELETE FROM optimization_proposals WHERE deal_id = ?", [proposal.deal_id])
    db.execute(
        """
        INSERT INTO optimization_proposals (
            deal_id,
            proposed_title,
            proposed_meta_title,
            proposed_meta_description,
            proposed_highlights,
            title_reasoning,
            generated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            proposal.deal_id,
            proposal.proposed_title,
            proposal.proposed_meta_title,
            proposal.proposed_meta_description,
            json.dumps(proposal.proposed_highlights),
            proposal.executive_summary,   # stored in title_reasoning column for now
            proposal.generated_at,
        ],
    )

    # ── proposal_recommendations (1:many) ─────────────────────────────────────
    db.execute(
        "DELETE FROM proposal_recommendations WHERE deal_id = ?", [proposal.deal_id]
    )
    for rec in proposal.recommendations:
        db.execute(
            """
            INSERT INTO proposal_recommendations (
                deal_id,
                priority_rank,
                category,
                recommendation,
                current_state,
                proposed_state,
                data_citation,
                expected_impact,
                impact_rationale
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                proposal.deal_id,
                rec.priority_rank,
                rec.category,
                rec.recommendation,
                rec.current_state,
                rec.proposed_state,
                rec.data_citation,
                rec.expected_impact,
                rec.impact_rationale,
            ],
        )


def get_proposal(deal_id: str) -> Optional[dict]:
    """Return the optimization_proposals row for a deal, or None."""
    db = get_db()
    row = db.execute(
        "SELECT * FROM optimization_proposals WHERE deal_id = ?", [deal_id]
    ).fetchone()
    if row is None:
        return None
    cols = [d[0] for d in db.description]
    return dict(zip(cols, row))


def get_recommendations(deal_id: str) -> list[dict]:
    """Return all recommendations for a deal, sorted by priority_rank."""
    db = get_db()
    rows = db.execute(
        """
        SELECT * FROM proposal_recommendations
        WHERE deal_id = ?
        ORDER BY priority_rank ASC
        """,
        [deal_id],
    ).fetchall()
    cols = [d[0] for d in db.description]
    return [dict(zip(cols, r)) for r in rows]
