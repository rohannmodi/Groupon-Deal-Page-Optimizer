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
        deal_quality_score=d.get("deal_quality_score", 5),
        key_insight=d.get("key_insight", ""),
        overall_verdict=d.get("overall_verdict", ""),
        review_themes=themes,
    )

    # Keep the numeric score and the strong/good/average/weak label internally
    # consistent with the value verdict and the merchant's review signals. The
    # model occasionally returns a score that contradicts its own verdict (e.g.
    # value_assessment="genuine_deal" with a 6/10 "average" score) or that
    # under-weights strong review volume. Reconcile them deterministically.
    _reconcile_deal_quality(synthesis, audit)

    log.info(
        "Research synthesis for %s: quality=%s, value=%s, themes=%d, tokens=%d",
        audit.deal_id,
        synthesis.deal_quality,
        synthesis.value_assessment,
        len(synthesis.review_themes),
        result.usage.total,
    )

    return synthesis, result.usage


def _reconcile_deal_quality(synthesis: ResearchSynthesis, audit: "DealAudit") -> None:
    """
    Align deal_quality_score and the deal_quality label with the value verdict
    AND the merchant's review signals.

    The numeric score and the verdict must tell the same story. We apply a
    value-verdict floor/ceiling and a review-signal floor to the score, then
    derive the label from the final score band:

      Value verdict:
        - genuine_deal  → score floored at 7
        - marginal      → score clamped to 4–6
        - overpriced    → score capped at 4
      Review signals (strong demand/social proof raise the floor):
        - rating ≥ 4.8 AND review_count ≥ 500 → floor 7.5
        - rating ≥ 4.5 AND review_count ≥ 200 → floor 7.0

      Label bands (from the final score):
        - ≥ 7.5 → strong
        - ≥ 6.5 → good
        - ≥ 4.0 → average
        - < 4.0 → weak
    """
    original_score = synthesis.deal_quality_score
    original_label = synthesis.deal_quality
    score = float(original_score)
    verdict = (synthesis.value_assessment or "").strip().lower()

    if verdict == "genuine_deal":
        score = max(score, 7.0)
    elif verdict == "overpriced":
        score = min(score, 4.0)
    elif verdict == "marginal":
        score = min(max(score, 4.0), 6.0)

    # Review-signal floor — strong rating + volume is a major positive signal.
    rating = audit.reviews_avg_rating or 0
    review_count = audit.reviews_count or 0
    if rating >= 4.8 and review_count >= 500:
        score = max(score, 7.5)
    elif rating >= 4.5 and review_count >= 200:
        score = max(score, 7.0)

    score = max(1.0, min(10.0, round(score, 1)))

    if score >= 7.5:
        label = "strong"
    elif score >= 6.5:
        label = "good"
    elif score >= 4.0:
        label = "average"
    else:
        label = "weak"

    synthesis.deal_quality_score = score
    synthesis.deal_quality = label

    if score != original_score or label != original_label:
        log.info(
            "Reconciled deal quality for %s: %s/%s → %s/%.1f "
            "(value_assessment=%s, rating=%s, reviews=%s)",
            audit.deal_id,
            original_label,
            original_score,
            label,
            score,
            verdict or "unknown",
            rating or "?",
            review_count or "?",
        )


def _build_research_context(audit: DealAudit, research: ResearchData) -> str:
    """
    Build the user message for the research synthesis prompt.

    The context is intentionally verbose — we want Claude to have the raw
    data so it can count theme mentions accurately rather than estimating.
    """
    sections: list[str] = []

    # ── Deal summary ────────────────────────────────────────────────────────
    import re as _re
    p = audit.primary_pricing
    price_line = "Unknown deal price"
    if p and p.deal_price:
        option_name = p.option_name or ""
        price_line = f"${p.deal_price:.2f}"
        if option_name:
            price_line += f" ({option_name})"
        # Detect group size from option name (e.g. "For 2", "For 3")
        for_n = _re.search(r'\bfor (\d+)\b', option_name, _re.IGNORECASE)
        if for_n:
            n = int(for_n.group(1))
            per_person = p.deal_price / n
            price_line += f" — PER PERSON EQUIVALENT: ${per_person:.2f}/person (group of {n})"
        if p.original_price:
            price_line += f"\nClaimed original: ${p.original_price:.2f}"
            if p.discount_pct:
                price_line += f" ({p.discount_pct:.0f}% off)"

    # Include all pricing tiers so AI knows this is a group-priced deal
    pricing_tiers = ""
    if len(audit.pricing_options) > 1:
        tier_lines = []
        for opt in audit.pricing_options[:8]:  # show up to 8 tiers
            if opt.deal_price:
                tier_lines.append(f"  {opt.option_name}: ${opt.deal_price:.2f}")
        if tier_lines:
            pricing_tiers = "\nAll pricing tiers (per group):\n" + "\n".join(tier_lines)
            if len(audit.pricing_options) > 8:
                pricing_tiers += f"\n  ... and {len(audit.pricing_options) - 8} more tiers"

    sections.append(f"""DEAL BEING ANALYZED
===================
Merchant: {audit.merchant_name or 'Unknown'}
Service / Title: {audit.title or 'Unknown'}
Category: {audit.category or 'Unknown'}
Location: {', '.join(filter(None, [audit.city, audit.state])) or 'Unknown'}
Groupon Price (primary option): {price_line}{pricing_tiers}
Groupon Rating: {audit.reviews_avg_rating or '?'}/5.0 ({audit.reviews_count or '?'} ratings)
URL: {audit.url}

PRICING NOTE: If the deal title says "For N" (e.g. "For 2", "For 4"), the Groupon price
covers the ENTIRE GROUP, not per person. Always normalize competitor/direct prices to the
same group size before comparing. e.g. if a competitor charges $55/person and Groupon is
$98.99 for 2, the competitor price for the same group is $110 — Groupon is CHEAPER.""")

    # ── Competitor prices ────────────────────────────────────────────────────
    # ── Research quality / failure report (Priorities 1, 2, 10) ────────────────
    quality = getattr(research, "quality", None)
    q_lines = [
        "RESEARCH DATA QUALITY REPORT",
        "=" * 40,
    ]
    if quality:
        q_lines += [
            f"Overall confidence: {quality.overall_confidence:.0%}",
            f"Competitor pricing: {quality.competitor_pricing_status}",
            f"Merchant reputation: {quality.merchant_reputation_status}",
            f"Category context: {quality.category_context_status}",
            f"Deal page pricing verifiable: {'YES' if quality.pricing_verifiable else 'NO'}",
            f"Sources: {quality.sources_accepted} accepted / "
            f"{quality.sources_rejected} rejected / {quality.sources_found} total",
        ]
        if quality.overall_confidence < 0.4:
            q_lines.append(
                "\n⚠ LOW CONFIDENCE: Research data is thin. Mark conclusions as "
                "tentative. Do NOT invent specific dollar comparisons."
            )
        if quality.pricing_verifiable:
            q_lines.append(
                "\n✓ PRICING VERIFIED: The deal page shows original/strikethrough prices. "
                "Do NOT say 'discount cannot be verified' — it can be verified from the page data."
            )
        else:
            q_lines.append(
                "\n⚠ PRICING NOT VERIFIED: Original prices not captured from the page — "
                "discount % claims from the page cannot be confirmed. Flag this."
            )
    sections.append("\n".join(q_lines))

    # ── Competitor prices with confidence scoring ─────────────────────────────
    merchant_name = audit.merchant_name or "the merchant"
    if research.competitors:
        comp_lines = []
        has_direct = any(
            c.notes and "DIRECT_BOOKING_CANDIDATE" in c.notes
            for c in research.competitors
        )
        high_conf_count = sum(
            1 for c in research.competitors
            if getattr(c, "sku_match_confidence", 0) >= 0.7
            and c.regular_price
            and not (c.notes and "DIRECT_BOOKING_CANDIDATE" in (c.notes or ""))
        )

        for c in research.competitors:
            price = (
                f"${c.regular_price:.2f}" if c.regular_price else "price unknown"
            )
            if c.sale_price:
                price += f" (sale: ${c.sale_price:.2f})"
            confidence = getattr(c, "sku_match_confidence", 0.5)
            is_merchant = getattr(c, "is_merchant", False)
            conf_label = (
                "HIGH CONFIDENCE" if confidence >= 0.7
                else "LOW CONFIDENCE" if confidence < 0.4
                else "MODERATE CONFIDENCE"
            )
            match_type = getattr(c, "match_type", "close")
            aggregator_flag = " [AGGREGATOR — exclude from comparisons]" if match_type == "aggregator" else ""
            line = (
                f"  - {c.competitor_name}: {price} "
                f"[SKU confidence: {confidence:.0%} — {conf_label}]"
                f" [match_type: {match_type}]{aggregator_flag}"
            )
            if c.notes:
                line += f" [{c.notes}]"
            line += f"\n    Source: {c.source_url}"
            comp_lines.append(line)

        if high_conf_count == 0:
            conf_warning = (
                "⚠ WARNING: No high-confidence (≥70%) competitor price matches found. "
                "Do NOT make direct price comparison claims (e.g. 'Groupon is $X more expensive'). "
                "Instead write: 'Possible market alternatives found — product match not verified.'"
            )
        else:
            conf_warning = (
                f"✓ {high_conf_count} high-confidence competitor price(s) found. "
                "These can be cited directly in price comparisons."
            )

        if has_direct:
            direct_note = (
                f"NOTE: One or more entries marked DIRECT_BOOKING_CANDIDATE appear to be "
                f"'{merchant_name}'s own website. Use ONLY those for groupon_vs_direct_savings."
            )
        else:
            direct_note = (
                f"IMPORTANT: None of these are '{merchant_name}'s own website. "
                "Set groupon_vs_direct_savings=null."
            )

        header = (
            f"ALTERNATIVE / COMPETITOR BUSINESS PRICES\n"
            f"=========================================\n"
            f"{conf_warning}\n{direct_note}"
        )
        sections.append(header + "\n\n" + "\n".join(comp_lines))
    else:
        sections.append(
            "ALTERNATIVE / COMPETITOR BUSINESS PRICES\n"
            "=========================================\n"
            "No competitor data collected. Set both groupon_vs_direct_savings=null "
            "and groupon_vs_competitor_savings=null. Do not estimate."
        )

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

        # Surface contamination warning prominently
        if ctx.notes and "CONTAMINATION DETECTED" in ctx.notes:
            cat_lines.append(
                "⚠ WARNING: Category context data was contaminated by cross-category "
                "search results and has been discarded. DO NOT use the price range "
                "from this section for market comparison claims."
            )
        else:
            if ctx.typical_price_low and ctx.typical_price_high:
                cat_lines.append(
                    f"Typical price range (validated): "
                    f"${ctx.typical_price_low:.0f}–${ctx.typical_price_high:.0f}"
                )
            if ctx.typical_discount_pct:
                cat_lines.append(
                    f"Typical Groupon discount for this category: {ctx.typical_discount_pct:.0f}%"
                )

        if ctx.notes:
            cat_lines.append(f"Notes: {ctx.notes}")

        # Only show search results that are clearly from the right category
        if ctx.search_results:
            cat_lines.append("\nValidated market pricing sources:")
            for sr in ctx.search_results[:5]:
                cat_lines.append(f"  [{sr.title[:80]}] {sr.snippet[:150]}")
                cat_lines.append(f"  Source: {sr.url}")
        sections.append("\n".join(cat_lines))

    # ── Sources ──────────────────────────────────────────────────────────────
    if research.sources:
        src_lines = ["RESEARCH SOURCES", "=" * 40]
        for s in research.sources:
            src_lines.append(f"  [{s.source_type}] {s.source_title or s.source_url} — {s.relevance}")
        sections.append("\n".join(src_lines))

    # ── Stale content warnings ───────────────────────────────────────────────
    stale_warnings = getattr(audit, "stale_content_warnings", [])
    if stale_warnings:
        stale_lines = [
            "STALE CONTENT WARNINGS — ACTIVE ON LIVE PAGE",
            "=" * 40,
            "The following outdated text was found on the current live deal page.",
            "Include the most egregious examples in red_flags.",
        ]
        for w in stale_warnings:
            stale_lines.append(
                f"  [{w.get('location', '?')}] {w.get('signal', '')}: "
                f'"{w.get("text", "")[:120]}"'
            )
        sections.append("\n".join(stale_lines))

    sections.append(
        "Please synthesize this research using the submit_research_synthesis tool. "
        "Every claim must reference specific data from above. "
        "Count theme mentions from the actual review texts provided."
    )

    return "\n\n".join(sections)
