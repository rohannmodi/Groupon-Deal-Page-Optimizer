"""
AI Audit Analyzer — Stage 1 AI.

Takes the structured DealAudit (from the scraper) and asks Claude to:
  1. Score the page on 6 dimensions (1–10 each)
  2. Identify specific content gaps (what's absent that customers need)
  3. Flag any misleading or unclear elements

Key prompt design choices:
  - Extended thinking is enabled for the gap-detection step. Identifying what's
    *absent* from a page requires genuine reasoning, not extraction — extended
    thinking lets Claude consider customer intent before responding.
  - Tool_use forces structured JSON output (no free-text responses to parse).
  - The prompt includes the full page content so Claude can reason from primary
    source, not from our parser's interpretation.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from models import AuditScores, DealAudit
from ai.client import call_with_tool, TokenUsage
from ai.schemas.audit_schema import AUDIT_TOOL_NAME, AUDIT_TOOL_SCHEMA

log = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent.parent / "config" / "prompts" / "audit_analysis.txt"

# Extended thinking budget for the gap-analysis reasoning step.
# 10k tokens ≈ 3–4 paragraphs of reasoning; enough for a careful analysis
# without running up the bill on every deal.
THINKING_BUDGET = 10_000


def analyze_audit(audit: DealAudit) -> tuple[AuditScores, TokenUsage]:
    """
    Run Claude audit analysis on a scraped deal.

    Returns (AuditScores, TokenUsage).
    Raises RuntimeError if the AI call fails after retries.
    """
    system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")
    user_message = _build_audit_context(audit)

    log.info("Running audit AI for deal %s (%s)", audit.deal_id, audit.title)

    result = call_with_tool(
        messages=[{"role": "user", "content": user_message}],
        tool_name=AUDIT_TOOL_NAME,
        tool_schema=AUDIT_TOOL_SCHEMA,
        system=system_prompt,
        max_tokens=6000,
        thinking_budget=THINKING_BUDGET,
    )

    scores = AuditScores(
        clarity_score=result.data["clarity_score"],
        trust_score=result.data["trust_score"],
        urgency_score=result.data["urgency_score"],
        seo_score=result.data["seo_score"],
        value_comm_score=result.data["value_comm_score"],
        completeness_score=result.data["completeness_score"],
        overall_score=result.data["overall_score"],
        content_gaps=result.data["content_gaps"],
        ai_flags=result.data.get("ai_flags", []),
        score_reasoning=result.data.get("score_reasoning", {}),
    )

    log.info(
        "Audit scores for %s: overall=%.1f gaps=%d flags=%d tokens=%d",
        audit.deal_id,
        scores.overall_score,
        len(scores.content_gaps),
        len(scores.ai_flags),
        result.usage.total,
    )

    return scores, result.usage


def _build_audit_context(audit: DealAudit) -> str:
    """
    Build the user message for the audit prompt.
    Includes all structured data from the scraper in a readable format.
    """
    p = audit.primary_pricing
    pricing_summary = "No pricing data extracted"
    if p:
        pricing_summary = (
            f"Deal price: ${p.deal_price:.2f}" if p.deal_price else "Unknown deal price"
        )
        if p.original_price:
            pricing_summary += f" (claimed original: ${p.original_price:.2f}"
            if p.discount_pct:
                pricing_summary += f", {p.discount_pct:.0f}% off"
            pricing_summary += ")"
    if len(audit.pricing_options) > 1:
        pricing_summary += f"\n{len(audit.pricing_options)} pricing tiers total"

    seo = audit.seo
    seo_section = f"""
Meta title: {seo.meta_title or 'MISSING'}
Meta description: {seo.meta_description or 'MISSING'} ({len(seo.meta_description or '')} chars)
H1: {seo.h1_text or 'MISSING'}
H2 headings ({len(seo.h2_texts)}): {'; '.join(seo.h2_texts[:5]) or 'none'}
Schema markup: {'Yes — ' + (seo.schema_type or 'type unknown') if seo.has_schema_markup else 'NO schema markup'}
OG title: {seo.og_title or 'MISSING'}
OG description: {seo.og_description or 'MISSING'}
Images on page: {seo.image_count} | Scripts: {seo.script_count}
""".strip()

    trust_section = "\n".join(
        f"- [{ts.signal_type}] {ts.signal_value}" for ts in audit.trust_signals
    ) or "None detected"

    urgency_section = "\n".join(
        f"- [{ue.element_type}] {ue.element_text}" for ue in audit.urgency_elements
    ) or "None detected"

    highlights_section = (
        "\n".join(f"- {h}" for h in audit.highlights) if audit.highlights
        else "NONE EXTRACTED"
    )

    fine_print_section = (
        "\n".join(f"- {fp}" for fp in audit.fine_print[:10]) if audit.fine_print
        else "NONE EXTRACTED"
    )

    images_section = (
        f"{len(audit.images)} images: "
        + ", ".join(
            f"{i.image_type}({'✓' if i.alt_text else '✗ no alt'})"
            for i in audit.images[:8]
        )
        if audit.images else "No images extracted"
    )

    reviews_section = (
        f"Rating: {audit.reviews_avg_rating or '?'}/5.0 "
        f"({audit.reviews_count or '?'} reviews)"
    )
    if audit.review_samples:
        reviews_section += "\nSample quotes:\n" + "\n".join(
            f'  "{rs.text[:200]}"' for rs in audit.review_samples[:3]
        )

    faqs_section = (
        "\n".join(f"Q: {f.question}\nA: {f.answer}" for f in audit.faqs[:5])
        if audit.faqs else "None"
    )

    return f"""DEAL PAGE AUDIT DATA
====================
URL: {audit.url}
Title: {audit.title or 'NOT FOUND'}
Subtitle: {audit.subtitle or 'None'}
Merchant: {audit.merchant_name or 'NOT FOUND'}
Category: {audit.category or 'Unknown'}
Location: {', '.join(filter(None, [audit.city, audit.state])) or 'Unknown'}
Description (first 500 chars): {(audit.description or 'None')[:500]}

PRICING
-------
{pricing_summary}

HIGHLIGHTS / WHAT YOU GET
--------------------------
{highlights_section}

FINE PRINT / TERMS
------------------
{fine_print_section}

IMAGES
------
{images_section}

REVIEWS
-------
{reviews_section}

TRUST SIGNALS
-------------
{trust_section}

URGENCY ELEMENTS
----------------
{urgency_section}

FAQs
----
{faqs_section}

SEO ELEMENTS
------------
{seo_section}

SCRAPE NOTES
------------
{audit.scrape_error or 'No errors'}
Scrape duration: {audit.scrape_duration_seconds or '?'}s

Please score this deal page and identify content gaps using the score_deal_audit tool."""
