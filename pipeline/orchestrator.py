"""
Pipeline orchestrator — coordinates the full processing pipeline for all deals.

Design:
  - asyncio with a semaphore for deal-level parallelism (up to N concurrent deals)
  - Within each deal, stages run sequentially (scrape → report)
  - Stage failures are isolated: one deal failing does not affect others
  - Checkpointing: already-completed stages are skipped on re-run
  - Rich progress display via a callback protocol (consumed by run.py)

Current stages implemented: scrape + report
Future stages (audit_ai, research, research_ai, proposal_ai) will be
dropped in here in order once their modules are built.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from models import DealAudit, DealInput
from pipeline.checkpoint import (
    mark_stage_failed,
    mark_stage_skipped,
    mark_stage_started,
    mark_stage_success,
    should_run_stage,
)
from reporter.json_reporter import write_audit_json
from reporter.markdown_reporter import write_audit_markdown
from scraper.groupon import scrape_deal
from storage.repositories.deal_repo import upsert_deal


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------

@dataclass
class DealResult:
    deal_id: str
    url: str
    status: str = "pending"       # pending | success | partial | failed
    stages_completed: list[str] = field(default_factory=list)
    stages_failed: list[str] = field(default_factory=list)
    error: Optional[str] = None
    audit: Optional[DealAudit] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None


# Progress callback type: called after each deal completes
ProgressCallback = Callable[[DealResult], None]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def run_pipeline(
    deals: list[DealInput],
    *,
    max_concurrency: int = 5,
    force_rerun: bool = False,
    on_progress: Optional[ProgressCallback] = None,
) -> list[DealResult]:
    """
    Process all deals concurrently (up to max_concurrency at a time).
    Returns a list of DealResult objects in completion order.
    """
    semaphore = asyncio.Semaphore(max_concurrency)
    results: list[DealResult] = []
    results_lock = asyncio.Lock()

    async def process_one(deal: DealInput) -> None:
        async with semaphore:
            result = await _process_deal(deal, force_rerun=force_rerun)
            async with results_lock:
                results.append(result)
            if on_progress:
                on_progress(result)

    await asyncio.gather(*[process_one(d) for d in deals], return_exceptions=False)
    return results


# ---------------------------------------------------------------------------
# Per-deal pipeline
# ---------------------------------------------------------------------------

async def _process_deal(deal: DealInput, force_rerun: bool = False) -> DealResult:
    result = DealResult(
        deal_id=deal.deal_id,
        url=deal.url,
        started_at=datetime.utcnow(),
    )

    try:
        # ── Stage: scrape ───────────────────────────────────────────────────
        audit = await _run_scrape_stage(deal, result, force_rerun)
        if audit is None:
            result.status = "failed"
            result.completed_at = datetime.utcnow()
            return result

        result.audit = audit

        # ── Stage: report ───────────────────────────────────────────────────
        await _run_report_stage(deal, audit, result, force_rerun)

        # ── Future stages (stubs — will be wired in later) ──────────────────
        # await _run_audit_ai_stage(deal, audit, result, force_rerun)
        # await _run_research_stage(deal, audit, result, force_rerun)
        # await _run_proposal_stage(deal, audit, result, force_rerun)

        result.status = "success" if not result.stages_failed else "partial"

    except Exception as exc:
        result.status = "failed"
        result.error = str(exc)

    result.completed_at = datetime.utcnow()
    return result


async def _run_scrape_stage(
    deal: DealInput,
    result: DealResult,
    force: bool,
) -> Optional[DealAudit]:
    stage = "scrape"

    if not should_run_stage(deal.deal_id, stage, force=force):
        mark_stage_skipped(deal.deal_id, stage)
        result.stages_completed.append(stage)
        # Load existing audit from DB for downstream stages
        return _load_audit_from_db(deal.deal_id)

    mark_stage_started(deal.deal_id, stage)
    try:
        audit = await scrape_deal(deal.deal_id, deal.url)

        if audit.scrape_error and not audit.title:
            # Total failure — no usable data
            raise RuntimeError(audit.scrape_error)

        # Persist to DB (even partial results are valuable)
        upsert_deal(audit)

        mark_stage_success(deal.deal_id, stage)
        result.stages_completed.append(stage)
        return audit

    except Exception as exc:
        err = str(exc)
        mark_stage_failed(deal.deal_id, stage, err)
        result.stages_failed.append(stage)
        result.error = err
        return None


async def _run_report_stage(
    deal: DealInput,
    audit: DealAudit,
    result: DealResult,
    force: bool,
) -> None:
    stage = "report"

    if not should_run_stage(deal.deal_id, stage, force=force):
        mark_stage_skipped(deal.deal_id, stage)
        result.stages_completed.append(stage)
        return

    mark_stage_started(deal.deal_id, stage)
    try:
        write_audit_json(audit)
        write_audit_markdown(audit)
        mark_stage_success(deal.deal_id, stage)
        result.stages_completed.append(stage)
    except Exception as exc:
        err = str(exc)
        mark_stage_failed(deal.deal_id, stage, err)
        result.stages_failed.append(stage)


# ---------------------------------------------------------------------------
# Helper: reload audit from DB (for resumed runs)
# ---------------------------------------------------------------------------

def _load_audit_from_db(deal_id: str) -> Optional[DealAudit]:
    """
    Reconstruct a minimal DealAudit from the DB row for downstream stages.
    Used when the scrape stage was already completed on a prior run.
    """
    from storage.repositories.deal_repo import get_deal
    row = get_deal(deal_id)
    if row is None:
        return None

    return DealAudit(
        deal_id=row["deal_id"],
        url=row["url"],
        title=row.get("title"),
        subtitle=row.get("subtitle"),
        merchant_name=row.get("merchant_name"),
        category=row.get("category"),
        city=row.get("city"),
        state=row.get("state"),
        description=row.get("description"),
        reviews_count=row.get("reviews_count"),
        reviews_avg_rating=row.get("reviews_avg_rating"),
        raw_html_path=row.get("raw_html_path"),
        # Child tables not loaded here — AI stages reload from DB directly
    )
