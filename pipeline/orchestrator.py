"""
Pipeline orchestrator — coordinates the full processing pipeline for all deals.

Design:
  - asyncio with a semaphore for deal-level parallelism (up to N concurrent deals)
  - Within each deal, stages run sequentially:
      scrape → report → audit_ai → research → research_ai
  - Stage failures are isolated: one deal failing does not affect others
  - Checkpointing: already-completed stages are skipped on re-run
  - Rich progress display via a callback protocol (consumed by run.py)

Stage key notes:
  - scrape:       Playwright + parsers → DealAudit in DB
  - report:       Write audit JSON + audit_summary.md
  - audit_ai:     Claude scores the audit page (sync → asyncio.to_thread)
  - research:     Yelp + Google + competitor scrapes (async natively)
  - research_ai:  Claude synthesizes research (sync → asyncio.to_thread)
                  Writes research.json + research_report.md
  - proposal_ai:  Claude generates ranked optimization proposal (sync → asyncio.to_thread)
                  Writes proposal.json + optimization_proposal.md
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from models import AuditScores, DealAudit, DealInput, OptimizationProposal, ResearchData, ResearchSynthesis
from pipeline.checkpoint import (
    mark_stage_failed,
    mark_stage_skipped,
    mark_stage_started,
    mark_stage_success,
    should_run_stage,
)
from reporter.json_reporter import write_audit_json, write_audit_scores_json, write_research_json
from reporter.markdown_reporter import write_audit_markdown
from reporter.proposal_markdown import write_proposal_markdown
from reporter.research_markdown import write_research_markdown
from scraper.groupon import scrape_deal
from storage.repositories.deal_repo import upsert_deal

log = logging.getLogger(__name__)


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
    stage_filter: Optional[str] = None,
    on_progress: Optional[ProgressCallback] = None,
) -> list[DealResult]:
    """
    Process all deals concurrently (up to max_concurrency at a time).
    Returns a list of DealResult objects in completion order.

    Args:
        deals:           List of DealInput to process.
        max_concurrency: Max deals running in parallel.
        force_rerun:     Re-run all stages even if already completed.
        stage_filter:    If set, only run stages up to and including this name
                         (e.g. "scrape", "audit_ai"). Useful for partial runs.
        on_progress:     Called after each deal finishes.
    """
    semaphore = asyncio.Semaphore(max_concurrency)
    results: list[DealResult] = []
    results_lock = asyncio.Lock()

    async def process_one(deal: DealInput) -> None:
        async with semaphore:
            result = await _process_deal(
                deal, force_rerun=force_rerun, stage_filter=stage_filter
            )
            async with results_lock:
                results.append(result)
            if on_progress:
                on_progress(result)

    await asyncio.gather(*[process_one(d) for d in deals], return_exceptions=False)
    return results


# ---------------------------------------------------------------------------
# Per-deal pipeline
# ---------------------------------------------------------------------------

async def _process_deal(
    deal: DealInput,
    force_rerun: bool = False,
    stage_filter: Optional[str] = None,
) -> DealResult:
    result = DealResult(
        deal_id=deal.deal_id,
        url=deal.url,
        started_at=datetime.utcnow(),
    )

    # Ordered stage names — used by stage_filter to decide where to stop
    _STAGE_ORDER = ["scrape", "report", "audit_ai", "research", "research_ai", "proposal_ai"]

    def _should_run(stage: str) -> bool:
        """Honour stage_filter: skip stages beyond the cutoff."""
        if stage_filter and stage_filter in _STAGE_ORDER:
            cutoff = _STAGE_ORDER.index(stage_filter)
            if _STAGE_ORDER.index(stage) > cutoff:
                return False
        return True

    try:
        # ── Stage: scrape ───────────────────────────────────────────────────
        audit = await _run_scrape_stage(deal, result, force_rerun)
        if audit is None:
            result.status = "failed"
            result.completed_at = datetime.utcnow()
            return result

        result.audit = audit

        # ── Stage: report ───────────────────────────────────────────────────
        if _should_run("report"):
            await _run_report_stage(deal, audit, result, force_rerun)

        # ── Stage: audit_ai ─────────────────────────────────────────────────
        scores: Optional[AuditScores] = None
        if _should_run("audit_ai"):
            scores = await _run_audit_ai_stage(deal, audit, result, force_rerun)

        # ── Stage: research ─────────────────────────────────────────────────
        research: Optional[ResearchData] = None
        if _should_run("research"):
            research = await _run_research_stage(deal, audit, result, force_rerun)

        # ── Stage: research_ai ──────────────────────────────────────────────
        synthesis: Optional[ResearchSynthesis] = None
        if _should_run("research_ai") and research is not None:
            synthesis = await _run_research_ai_stage(deal, audit, research, scores, result, force_rerun)

        # ── Stage: proposal_ai ──────────────────────────────────────────────
        if _should_run("proposal_ai") and scores is not None and synthesis is not None:
            await _run_proposal_ai_stage(deal, audit, scores, research, synthesis, result, force_rerun)
        elif _should_run("proposal_ai") and (scores is None or synthesis is None):
            log.warning(
                "Skipping proposal_ai for %s — missing %s",
                deal.deal_id,
                "scores" if scores is None else "synthesis",
            )

        result.status = "success" if not result.stages_failed else "partial"

    except Exception as exc:
        log.exception("Unhandled error processing deal %s", deal.deal_id)
        result.status = "failed"
        result.error = str(exc)

    result.completed_at = datetime.utcnow()
    return result


# ---------------------------------------------------------------------------
# Stage implementations
# ---------------------------------------------------------------------------

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
        log.warning("Report stage failed for %s: %s", deal.deal_id, err)
        mark_stage_failed(deal.deal_id, stage, err)
        result.stages_failed.append(stage)


async def _run_audit_ai_stage(
    deal: DealInput,
    audit: DealAudit,
    result: DealResult,
    force: bool,
) -> Optional[AuditScores]:
    """
    Call the Claude audit analyzer (synchronous) via asyncio.to_thread so
    it doesn't block the event loop while waiting on the Anthropic API.
    """
    stage = "audit_ai"

    if not should_run_stage(deal.deal_id, stage, force=force):
        mark_stage_skipped(deal.deal_id, stage)
        result.stages_completed.append(stage)
        # Load from DB for downstream stages that need scores
        return _load_scores_from_db(deal.deal_id)

    mark_stage_started(deal.deal_id, stage)
    try:
        # Import here to avoid circular imports at module load time
        from ai.audit_analyzer import analyze_audit
        from storage.repositories.research_repo import upsert_audit_scores

        # analyze_audit is synchronous (uses tenacity + Anthropic SDK sync client)
        # Run in a thread so we don't block the asyncio event loop
        scores, usage = await asyncio.to_thread(analyze_audit, audit)

        upsert_audit_scores(
            deal.deal_id,
            scores,
            usage_prompt=usage.prompt,
            usage_completion=usage.completion,
        )
        write_audit_scores_json(deal.deal_id, scores)

        log.info(
            "Audit AI done for %s: overall=%.1f, cost≈$%.4f",
            deal.deal_id,
            scores.overall_score,
            usage.estimated_cost_usd,
        )
        mark_stage_success(deal.deal_id, stage)
        result.stages_completed.append(stage)
        return scores

    except Exception as exc:
        err = str(exc)
        log.warning("Audit AI stage failed for %s: %s", deal.deal_id, err)
        mark_stage_failed(deal.deal_id, stage, err)
        result.stages_failed.append(stage)
        return None


async def _run_research_stage(
    deal: DealInput,
    audit: DealAudit,
    result: DealResult,
    force: bool,
) -> Optional[ResearchData]:
    """
    Gather competitive research: Yelp, Google, competitor prices, category
    context. researcher.runner.research_deal() is natively async.
    """
    stage = "research"

    if not should_run_stage(deal.deal_id, stage, force=force):
        mark_stage_skipped(deal.deal_id, stage)
        result.stages_completed.append(stage)
        # Can't fully reconstruct ResearchData from DB without a lot of joins —
        # return a stub so downstream stages gracefully degrade.
        return _load_research_stub_from_db(deal.deal_id)

    mark_stage_started(deal.deal_id, stage)
    try:
        from researcher.runner import research_deal
        from storage.repositories.research_repo import upsert_research_data

        research = await research_deal(audit)
        upsert_research_data(research)

        comp_count = len(research.competitors)
        yelp_ok = research.yelp is not None
        google_ok = research.google is not None
        log.info(
            "Research done for %s: %d competitors, yelp=%s, google=%s",
            deal.deal_id,
            comp_count,
            yelp_ok,
            google_ok,
        )
        mark_stage_success(deal.deal_id, stage)
        result.stages_completed.append(stage)
        return research

    except Exception as exc:
        err = str(exc)
        log.warning("Research stage failed for %s: %s", deal.deal_id, err)
        mark_stage_failed(deal.deal_id, stage, err)
        result.stages_failed.append(stage)
        return None


async def _run_research_ai_stage(
    deal: DealInput,
    audit: DealAudit,
    research: ResearchData,
    scores: Optional[AuditScores],
    result: DealResult,
    force: bool,
) -> Optional[ResearchSynthesis]:
    """
    Call Claude to synthesize the research into a structured verdict
    (sync via asyncio.to_thread). Then write research.json + research_report.md.
    Returns the ResearchSynthesis for use by the proposal stage.
    """
    stage = "research_ai"

    if not should_run_stage(deal.deal_id, stage, force=force):
        mark_stage_skipped(deal.deal_id, stage)
        result.stages_completed.append(stage)
        return _load_synthesis_from_db(deal.deal_id)

    mark_stage_started(deal.deal_id, stage)
    try:
        from ai.research_synthesizer import synthesize_research
        from storage.repositories.research_repo import upsert_research_synthesis

        # synthesize_research is synchronous — run in thread pool
        synthesis, usage = await asyncio.to_thread(synthesize_research, audit, research)

        # Persist AI output to DB
        upsert_research_synthesis(deal.deal_id, synthesis)

        # Write structured JSON + human-readable markdown
        write_research_json(deal.deal_id, research, synthesis)
        write_research_markdown(deal.deal_id, audit, research, synthesis, scores)

        log.info(
            "Research AI done for %s: quality=%s, value=%s, cost≈$%.4f",
            deal.deal_id,
            synthesis.deal_quality,
            synthesis.value_assessment,
            usage.estimated_cost_usd,
        )
        mark_stage_success(deal.deal_id, stage)
        result.stages_completed.append(stage)
        return synthesis

    except Exception as exc:
        err = str(exc)
        log.warning("Research AI stage failed for %s: %s", deal.deal_id, err)
        mark_stage_failed(deal.deal_id, stage, err)
        result.stages_failed.append(stage)
        return None


async def _run_proposal_ai_stage(
    deal: DealInput,
    audit: DealAudit,
    scores: AuditScores,
    research: ResearchData,
    synthesis: ResearchSynthesis,
    result: DealResult,
    force: bool,
) -> None:
    """
    Call Claude to generate the full optimization proposal
    (sync via asyncio.to_thread). Writes proposal JSON + optimization_proposal.md.
    """
    stage = "proposal_ai"

    if not should_run_stage(deal.deal_id, stage, force=force):
        mark_stage_skipped(deal.deal_id, stage)
        result.stages_completed.append(stage)
        return

    mark_stage_started(deal.deal_id, stage)
    try:
        from ai.proposal_generator import generate_proposal
        from reporter.json_reporter import write_proposal_json
        from storage.repositories.proposal_repo import upsert_proposal

        # generate_proposal is synchronous (extended thinking + Anthropic SDK)
        proposal, usage = await asyncio.to_thread(
            generate_proposal, audit, scores, research, synthesis
        )

        # Persist to DB
        upsert_proposal(proposal, usage_prompt=usage.prompt, usage_completion=usage.completion)

        # Write human-readable markdown
        write_proposal_markdown(deal.deal_id, audit, proposal, scores, synthesis)

        # Write structured JSON
        write_proposal_json(deal.deal_id, proposal)

        log.info(
            "Proposal AI done for %s: %d recommendations, cost≈$%.4f",
            deal.deal_id,
            len(proposal.recommendations),
            usage.estimated_cost_usd,
        )
        mark_stage_success(deal.deal_id, stage)
        result.stages_completed.append(stage)

    except Exception as exc:
        err = str(exc)
        log.warning("Proposal AI stage failed for %s: %s", deal.deal_id, err)
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


def _load_scores_from_db(deal_id: str) -> Optional[AuditScores]:
    """
    Reconstruct AuditScores from the audit_scores DB table.
    Used when audit_ai was already completed on a prior run.
    """
    import json as _json
    from storage.repositories.research_repo import get_audit_scores
    row = get_audit_scores(deal_id)
    if row is None:
        return None
    try:
        return AuditScores(
            clarity_score=row["clarity_score"],
            trust_score=row["trust_score"],
            urgency_score=row["urgency_score"],
            seo_score=row["seo_score"],
            value_comm_score=row["value_comm_score"],
            completeness_score=row["completeness_score"],
            overall_score=row["overall_score"],
            content_gaps=_json.loads(row.get("content_gaps") or "[]"),
            ai_flags=_json.loads(row.get("ai_flags") or "[]"),
        )
    except Exception as exc:
        log.warning("Could not reconstruct AuditScores for %s: %s", deal_id, exc)
        return None


def _load_synthesis_from_db(deal_id: str) -> Optional[ResearchSynthesis]:
    """
    Reconstruct a ResearchSynthesis from the DB for resumed runs.
    Used when research_ai was already completed on a prior run.
    """
    import json as _json
    from models import ReviewTheme
    from storage.repositories.research_repo import get_research_synthesis, get_review_themes

    row = get_research_synthesis(deal_id)
    if row is None:
        return None
    try:
        themes = [
            ReviewTheme(
                theme=t["theme"],
                sentiment=t["sentiment"],
                mention_count=t["mention_count"],
                sample_quote=t.get("sample_quote", ""),
                platform=t.get("platform", "unknown"),
            )
            for t in get_review_themes(deal_id)
        ]
        return ResearchSynthesis(
            value_assessment=row["value_assessment"],
            value_reasoning=row.get("value_reasoning", ""),
            groupon_vs_direct_savings=row.get("groupon_vs_direct_savings"),
            groupon_vs_competitor_savings=row.get("groupon_vs_competitor_savings"),
            merchant_differentiators=_json.loads(row.get("merchant_differentiators") or "[]"),
            red_flags=_json.loads(row.get("red_flags") or "[]"),
            deal_quality=row["deal_quality"],
            review_themes=themes,
        )
    except Exception as exc:
        log.warning("Could not reconstruct ResearchSynthesis for %s: %s", deal_id, exc)
        return None


def _load_research_stub_from_db(deal_id: str) -> ResearchData:
    """
    Build a partial ResearchData from the DB for resumed runs.
    Competitor prices are loaded; Yelp/Google objects are not fully
    reconstructed (would need significant model mapping). Synthesis AI
    will work with whatever is available.
    """
    import json as _json
    from models import CompetitorPrice, ResearchSource
    from storage.repositories.research_repo import get_competitor_prices

    comp_rows = get_competitor_prices(deal_id)
    competitors = [
        CompetitorPrice(
            competitor_name=r["competitor_name"],
            service_name=r["service_name"],
            regular_price=r.get("regular_price"),
            sale_price=r.get("sale_price"),
            source_url=r["source_url"],
            notes=r.get("notes"),
        )
        for r in comp_rows
    ]

    return ResearchData(
        deal_id=deal_id,
        competitors=competitors,
        # yelp/google/category_context left as None — partial data is OK
    )
