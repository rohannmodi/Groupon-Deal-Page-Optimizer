"""
Checkpoint logic — determines which pipeline stages need to run for a given deal.

On re-run, the orchestrator calls `should_run_stage()` before each stage.
A stage is skipped if its last recorded status in pipeline_runs is 'success'.
This means you can re-run the whole pipeline after a partial failure without
re-scraping or re-paying for AI calls.
"""

from __future__ import annotations

from datetime import datetime

from models import PipelineRun
from storage.repositories.deal_repo import get_stage_status, upsert_pipeline_run


# All pipeline stages in execution order
STAGES = [
    "scrape",
    "parse",       # currently combined with scrape in groupon.py
    "audit_ai",    # Stage 1 AI (future)
    "research",    # Stage 2 web research (future)
    "research_ai", # Stage 2 AI synthesis (future)
    "proposal_ai", # Stage 3 AI (future)
    "report",      # Output file generation
]


def should_run_stage(deal_id: str, stage: str, force: bool = False) -> bool:
    """
    Return True if the stage should execute for this deal.

    A stage is skipped iff:
      - force=False AND
      - the most recent pipeline_run for (deal_id, stage) has status='success'
    """
    if force:
        return True
    status = get_stage_status(deal_id, stage)
    return status != "success"


def mark_stage_started(deal_id: str, stage: str) -> None:
    upsert_pipeline_run(
        PipelineRun(
            deal_id=deal_id,
            stage=stage,
            status="running",
            started_at=datetime.utcnow(),
        )
    )


def mark_stage_success(deal_id: str, stage: str) -> None:
    upsert_pipeline_run(
        PipelineRun(
            deal_id=deal_id,
            stage=stage,
            status="success",
            started_at=None,
            completed_at=datetime.utcnow(),
        )
    )


def mark_stage_failed(deal_id: str, stage: str, error: str, retry_count: int = 0) -> None:
    upsert_pipeline_run(
        PipelineRun(
            deal_id=deal_id,
            stage=stage,
            status="failed",
            started_at=None,
            completed_at=datetime.utcnow(),
            error_message=error,
            retry_count=retry_count,
        )
    )


def mark_stage_skipped(deal_id: str, stage: str) -> None:
    upsert_pipeline_run(
        PipelineRun(
            deal_id=deal_id,
            stage=stage,
            status="skipped",
            completed_at=datetime.utcnow(),
        )
    )
