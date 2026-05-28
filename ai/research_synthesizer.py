"""
AI Research Synthesizer — Stage 2 AI.

Takes the raw ResearchData (competitor prices, Yelp/Google reviews, category
context) and asks Claude to:
  1. Code recurring themes from the review corpus with exact mention counts
  2. Make a specific deal value verdict (genuine_deal | marginal | overpriced)
     with exact dollar comparisons
  3. Identify merchant differentiators from review patterns
  4. Flag red flags backed by specific data points

Key prompt design choices:
  - The prompt provides ALL raw review texts so Claude can actually count theme
    mentions — not just summarise. This produces defensible counts.
  - Claude is explicitly told every field must cite a specific source. Generic
    advice triggers a re-prompt (handled by caller if needed).
  - No extended thinking here — the task is synthesis, not gap-detection.
    The large context window does the heavy lifting.
"""

from __future__ import annotations

import logging
from pathlib import Path

from models import DealAudit, ResearchData, ResearchSynthesis, ReviewTheme
from ai.client import call_with_tool, TokenUsage
from ai.schemas.research_schema import RESEARCH_TOOL_NAME, RESEARCH_TOOL_SCHEMA

log = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent.parent / "config" / "prompts" / "research_synthesis.txt"


def synthesize_research(
    audit: DealAudit,
    research: ResearchData,
) -> tuple[ResearchSynthesis, TokenUsage]:
    """
    Run Claude research synthesis for a deal.

    Returns (ResearchSynthesis, TokenUsage).
    """
    system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")
    user_message = _build_research_context(audit, research)

    log.info(
        "Running research AI for deal %s — %d competitors, yelp=%s, google=%s",
        audit.deal_id,
        len(research.competitors),
        "yes" if research.yelp else "no",
        "yes" if research.google else "no",
    )

    result = call_with_tool(
        messages=[{"role": "user", "content": user_message}],
        tool_name=RESEARCH_TOOL_NAME,
        tool_schema=RESEARCH_TOOL_SCHEMA,
        system=system_prompt,
        max_tokens=8000,   # larger budget for the rich context + theme analysis
    )

    d = result.data
    themes = [
        ReviewTheme(
            theme=t["theme"],
            sentiment=t["sentiment"],
            mention_count=t["mention_count"],
            sample_quote=t["sample_quote"],
            platform=t["platform"],
        )
        for t in d.get("review_themes", [])
    ]

    synthesis = ResearchSynthesis(
        value_assessment=d["value_assessment"],
        value_reasoning=d["value_reasoning"],
        groupon_vs_direct_savings=d.get("groupon_vs_direct_savings"),
        groupon_vs_competitor_savings=d.get("groupon_vs_competitor_savings"),
        merchant_differentiators=d.get("merchant_differentiators", []),
        red_flags=d.get("red_flags", []),
        deal_quality=d["deal_quality"],
        review_themes=themes,
    )

    log.info(
        "Research synthesis for %s: quality=%s, value=%s, themes=%d, tokens=%d",
        audit.deal_id,
        synthesis.deal_quality,
        synthesis.value_assessment,
        len(synthesis.review_themes),
        result.usage.total,
    )

    return synthesis, result.usage


def _build_research_context(audit: DealAudit, research: ResearchData) -> str:
    """
    Build the user message for the research synthesis prompt.

    The context is intentionally verbose — we want Claude to have the raw
    data so it can count theme mentions accurately rather than estimating.
    """
    sections: list[str] = []

    # ── Deal summary ────────────────────────────────────────────────────────
    p = audit.primary_pricing
    price_line = "Unknown deal price"
    if p and p.deal_price:
        price_line = f"${p.deal_price:.2f}"
        if p.original_price:
            price_line += f" (claimed original: ${p.original_price:.2f}"
            if p.discount_pct:
                price_line += f", {p.discount_pct:.0f}% off"
            price_line += ")"

    sections.append(f"""DEAL BEING ANALYZED
===================
Merchant: {audit.merchant_name or 'Unknown'}
Service / Title: {audit.title or 'Unknown'}
Category: {audit.category or 'Unknown'}
Location: {', '.join(filter(None, [audit.city, audit.state])) or 'Unknown'}
Groupon Price: {price_line}
Groupon Rating: {audit.reviews_avg_rating or '?'}/5.0 ({audit.reviews_count or '?'} ratings)
URL: {audit.url}""")

    # ── Competitor prices ────────────────────────────────────────────────────
    if research.competitors:
        comp_lines = []
        for c in research.competitors:
            price = (
                f"${c.regular_price:.2f}" if c.regular_price else "price unknown"
            )
            if c.sale_price:
                price += f" (sale: ${c.sale_price:.2f})"
            line = f"  - {c.competitor_name}: {c.service_name} — {price}"
            if c.notes:
                line += f" [{c.notes}]"
            line += f"\n    Source: {c.source_url}"
            comp_lines.append(line)
        sections.append("COMPETITOR PRICES\n================\n" + "\n".join(comp_lines))
    else:
        sections.append("COMPETITOR PRICES\n================\nNo competitor data collected.")

    # ── Yelp data ────────────────────────────────────────────────────────────
    if research.yelp:
        y = research.yelp
        yelp_block = [
            f"YELP DATA — {y.name or audit.merchant_name}",
            "=" * 40,
            f"Rating: {y.rating}/5.0 ({y.review_count} reviews)" if y.rating else "Rating: not found",
            f"URL: {y.url}",
            f"Categories: {', '.join(y.categories) or 'none'}",
        ]
        if y.review_texts:
            yelp_block.append(
                f"\nREVIEW TEXTS ({len(y.review_texts)} provided — "
                "count theme mentions carefully):"
            )
            for i, text in enumerate(y.review_texts[:20], 1):   # cap at 20
                yelp_block.append(f"[{i}] {text[:400]}")
        sections.append("\n".join(yelp_block))
    else:
        sections.append("YELP DATA\n=========\nNo Yelp data collected.")

    # ── Google data ──────────────────────────────────────────────────────────
    if research.google:
        g = research.google
        google_block = [
            "GOOGLE BUSINESS DATA",
            "=" * 40,
        ]
        if g.rating:
            google_block.append(f"Rating: {g.rating}/5.0 ({g.review_count} reviews)")
        if g.address:
            google_block.append(f"Address: {g.address}")
        if g.source_url:
            google_block.append(f"Source: {g.source_url}")
        sections.append("\n".join(google_block))
    else:
        sections.append("GOOGLE DATA\n===========\nNo Google data collected.")

    # ── Category context ─────────────────────────────────────────────────────
    if research.category_context:
        ctx = research.category_context
        cat_lines = [f"CATEGORY CONTEXT — {ctx.category} in {ctx.city}", "=" * 40]
        if ctx.typical_price_low and ctx.typical_price_high:
            cat_lines.append(
                f"Typical price range: ${ctx.typical_price_low:.0f}–${ctx.typical_price_high:.0f}"
            )
        if ctx.typical_discount_pct:
            cat_lines.append(f"Typical Groupon discount for this category: {ctx.typical_discount_pct:.0f}%")
        if ctx.notes:
            cat_lines.append(f"Notes: {ctx.notes}")
        if ctx.search_results:
            cat_lines.append("\nSearch result snippets for context:")
            for sr in ctx.search_results[:5]:
                cat_lines.append(f"  [{sr.title}] {sr.snippet[:200]}")
                cat_lines.append(f"  Source: {sr.url}")
        sections.append("\n".join(cat_lines))

    # ── Sources ──────────────────────────────────────────────────────────────
    if research.sources:
        src_lines = ["RESEARCH SOURCES", "=" * 40]
        for s in research.sources:
            src_lines.append(f"  [{s.source_type}] {s.source_title or s.source_url} — {s.relevance}")
        sections.append("\n".join(src_lines))

    sections.append(
        "Please synthesize this research using the submit_research_synthesis tool. "
        "Every claim must reference specific data from above. "
        "Count theme mentions from the actual review texts provided."
    )

    return "\n\n".join(sections)
