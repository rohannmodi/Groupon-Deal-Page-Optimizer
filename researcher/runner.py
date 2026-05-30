"""
Research runner — orchestrates all research sub-tasks for a single deal.

Runs Yelp scrape, Google lookup, competitor search, and category benchmarking
in parallel (asyncio.gather). Each sub-task fails independently — a Yelp
timeout doesn't block competitor research.

Returns a ResearchData object ready to be handed to the AI synthesizer.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from models import (
    CategoryContext,
    CompetitorPrice,
    DealAudit,
    GoogleData,
    ResearchData,
    ResearchQuality,
    YelpData,
)
from researcher.category_benchmarker import get_category_context
from researcher.competitor_finder import find_competitors
from researcher.google_reviews import scrape_google_business
from researcher.source_tracker import SourceTracker
from researcher.yelp_scraper import scrape_yelp

log = logging.getLogger(__name__)


async def research_deal(audit: DealAudit) -> ResearchData:
    """
    Run all research tasks in parallel for a single deal.

    Each task is wrapped in a timeout + exception guard so one failure
    cannot prevent the others from completing.
    """
    tracker = SourceTracker(audit.deal_id)
    merchant = audit.merchant_name or ""
    city = audit.city or ""
    category = audit.category or ""
    deal_price = audit.primary_pricing.deal_price if audit.primary_pricing else None

    # Derive a clean service type for search queries
    service_type = _infer_service_type(audit)

    log.info(
        "Researching deal %s — merchant=%r, service=%r, city=%r, price=$%s",
        audit.deal_id, merchant, service_type, city,
        f"{deal_price:.2f}" if deal_price else "unknown",
    )

    # ── Run all tasks concurrently ────────────────────────────────────────────
    yelp_task = asyncio.create_task(
        _safe_run("yelp", scrape_yelp(merchant, city))
    )
    google_task = asyncio.create_task(
        _safe_run("google", scrape_google_business(merchant, city))
    )
    competitor_task = asyncio.create_task(
        _safe_run("competitors", find_competitors(
            service_type, city, deal_price,
            merchant_name=merchant,
            category=category,
        ))
    )
    category_task = asyncio.create_task(
        _safe_run("category", get_category_context(
            category, city, deal_price,
            service_type=service_type,  # critical: drives category detection
        ))
    )

    yelp_data, google_data, competitors, category_context = await asyncio.gather(
        yelp_task, google_task, competitor_task, category_task
    )

    # ── Track sources ─────────────────────────────────────────────────────────
    if yelp_data and yelp_data.url:
        tracker.add(
            source_type="yelp",
            url=yelp_data.url,
            title=f"Yelp: {yelp_data.name or merchant}",
            relevance="review",
        )

    if google_data and google_data.source_url:
        tracker.add(
            source_type="google",
            url=google_data.source_url,
            title=f"Google: {google_data.name or merchant}",
            relevance="review",
        )

    if competitors:
        for comp in competitors:
            tracker.add(
                source_type="direct_scrape",
                url=comp.source_url,
                title=comp.competitor_name,
                relevance="competitor_price",
            )

    if category_context:
        for sr in (category_context.search_results or []):
            tracker.add_search_results([sr], relevance="category_context")

    # ── Compute research quality / failure report ─────────────────────────────
    quality = _compute_research_quality(
        audit=audit,
        competitors=competitors or [],
        yelp_data=yelp_data,
        google_data=google_data,
        category_context=category_context,
    )

    log.info(
        "Research complete for %s: yelp=%s, google=%s, competitors=%d, "
        "confidence=%.2f, pricing_verifiable=%s",
        audit.deal_id,
        "✓" if yelp_data else "✗",
        "✓" if google_data else "✗",
        len(competitors or []),
        quality.overall_confidence,
        quality.pricing_verifiable,
    )

    return ResearchData(
        deal_id=audit.deal_id,
        yelp=yelp_data,
        google=google_data,
        competitors=competitors or [],
        category_context=category_context,
        sources=tracker.get_all(),
        quality=quality,
    )


async def _safe_run(label: str, coro):
    """
    Run a coroutine with a timeout and exception guard.
    Returns None (or []) on failure instead of propagating the exception.
    """
    try:
        return await asyncio.wait_for(coro, timeout=45.0)
    except asyncio.TimeoutError:
        log.warning("Research task %r timed out after 45s", label)
        return _empty_for(label)
    except Exception as exc:
        log.warning("Research task %r failed: %s", label, exc)
        return _empty_for(label)


def _empty_for(label: str):
    """Return an appropriate empty value for each task type."""
    if label == "competitors":
        return []
    return None


def _compute_research_quality(
    audit: DealAudit,
    competitors: list,
    yelp_data,
    google_data,
    category_context,
) -> ResearchQuality:
    """
    Assess data quality across all research dimensions.
    Returns a ResearchQuality object for failure reporting (Priority 10).
    """
    # --- Competitor pricing status ---
    high_conf = [c for c in competitors if c.sku_match_confidence >= 0.7 and c.regular_price]
    low_conf_priced = [c for c in competitors if c.sku_match_confidence < 0.7 and c.regular_price]
    no_price = [c for c in competitors if not c.regular_price]

    if high_conf:
        comp_status = "verified"
    elif low_conf_priced:
        comp_status = "low_confidence"
    elif no_price and (len(competitors) > 0):
        comp_status = "partial_data"
    else:
        comp_status = "no_data"

    # --- Merchant reputation ---
    if yelp_data and google_data:
        rep_status = "verified"
    elif yelp_data or google_data:
        rep_status = "partial_data"
    else:
        rep_status = "no_data"

    # --- Category context ---
    if category_context and category_context.typical_price_low:
        cat_status = "validated"
    elif category_context and category_context.search_results:
        cat_status = "insufficient"
    else:
        cat_status = "no_data"

    # --- Pricing verifiable (Priority 1) ---
    # Check if the scraper captured original prices for the primary option
    p = audit.primary_pricing
    pricing_verifiable = (
        p is not None
        and p.deal_price is not None
        and p.original_price is not None
        and p.discount_pct is not None
    )

    # --- Overall confidence ---
    scores = []
    if comp_status == "verified":
        scores.append(1.0)
    elif comp_status == "low_confidence":
        scores.append(0.4)
    elif comp_status == "partial_data":
        scores.append(0.2)
    else:
        scores.append(0.0)

    if rep_status == "verified":
        scores.append(1.0)
    elif rep_status == "partial_data":
        scores.append(0.6)
    else:
        scores.append(0.0)

    if cat_status == "validated":
        scores.append(0.8)
    elif cat_status == "insufficient":
        scores.append(0.3)
    else:
        scores.append(0.0)

    overall_confidence = round(sum(scores) / len(scores), 2) if scores else 0.0

    # Source tracking
    total_sources = len(competitors) + (1 if yelp_data else 0) + (1 if google_data else 0)

    return ResearchQuality(
        competitor_pricing_status=comp_status,
        merchant_reputation_status=rep_status,
        category_context_status=cat_status,
        pricing_verifiable=pricing_verifiable,
        overall_confidence=overall_confidence,
        sources_found=total_sources + len(no_price),
        sources_accepted=len(high_conf) + len(low_conf_priced),
        sources_rejected=len(no_price),
    )


def _infer_service_type(audit: DealAudit) -> str:
    """
    Derive a clean, searchable service type from the audit data.
    Prefers the title over the category since the title is more specific.
    """
    import re as _re
    title = (audit.title or "").lower()
    category = (audit.category or "").lower()

    # Strip common Groupon boilerplate — use word boundaries so "off" doesn't mangle "offering"
    for phrase in ("groupon", "deal", "discount", r"\boff\b", r"\bsave\b", r"\bat\b"):
        title = _re.sub(phrase, "", title)

    title = _re.sub(r"\s+", " ", title).strip()

    # Use the title if it's informative (longer than category)
    if len(title) > len(category):
        return title[:60]

    return category.strip() or (audit.title or "service")[:60]
