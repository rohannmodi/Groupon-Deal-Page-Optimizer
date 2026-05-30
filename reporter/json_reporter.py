"""
JSON reporter — serializes pipeline outputs to per-deal JSON files.

Writes:
  outputs/{deal_id}/1_audit/audit.json
  outputs/{deal_id}/1_audit/audit_scores.json
  outputs/{deal_id}/2_research/research.json
  outputs/{deal_id}/3_proposal/proposal.json
"""

from __future__ import annotations

import json
from pathlib import Path

from models import AuditScores, DealAudit, OptimizationProposal, ResearchData, ResearchSynthesis
from config.settings import settings


# ---------------------------------------------------------------------------
# Stage 1 — Audit
# ---------------------------------------------------------------------------

def write_audit_json(audit: DealAudit) -> Path:
    """Write audit.json to outputs/{deal_id}/1_audit/ and return the path."""
    out_dir = _stage_dir(audit.deal_id, "1_audit")
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / "audit.json"
    out_path.write_text(
        json.dumps(audit.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return out_path


def write_audit_scores_json(deal_id: str, scores: AuditScores) -> Path:
    """Write audit_scores.json alongside the audit."""
    out_dir = _stage_dir(deal_id, "1_audit")
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / "audit_scores.json"
    out_path.write_text(
        json.dumps(scores.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return out_path


# ---------------------------------------------------------------------------
# Stage 2 — Research
# ---------------------------------------------------------------------------

def write_research_json(deal_id: str, data: ResearchData, synthesis: ResearchSynthesis) -> Path:
    """Write research.json to outputs/{deal_id}/2_research/."""
    out_dir = _stage_dir(deal_id, "2_research")
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "raw_research": data.model_dump(mode="json"),
        "synthesis": synthesis.model_dump(mode="json"),
        # Priority 10: failure reporting / quality metadata
        "data_quality": getattr(data, "quality", None) and data.quality.model_dump(mode="json"),
    }
    out_path = out_dir / "research.json"
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return out_path


# ---------------------------------------------------------------------------
# Stage 3 — Proposal
# ---------------------------------------------------------------------------

def write_proposal_json(deal_id: str, proposal: OptimizationProposal) -> Path:
    """Write proposal.json to outputs/{deal_id}/3_proposal/."""
    out_dir = _stage_dir(deal_id, "3_proposal")
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / "proposal.json"
    out_path.write_text(
        json.dumps(proposal.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return out_path


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------

def _stage_dir(deal_id: str, stage_folder: str) -> Path:
    return settings.outputs_dir / deal_id / stage_folder
