"""
AI Proposal Generator — Stage 3 AI.

Takes the full picture — DealAudit + AuditScores + ResearchData +
ResearchSynthesis — and asks Claude to produce a concrete, prioritized
optimization proposal.

Key design choices:
  - Extended thinking is enabled (budget_tokens=16_000). Generating a
    well-ordered, mutually-non-overlapping recommendation list requires
    genuine reasoning across all four data sources. Extended thinking
    produces substantially better priority ordering and avoids Claude
    listing the same insight under two different categories.
  - max_tokens is set high (8192) to accommodate full proposed copy +
    8-10 recommendations each with five substantial fields.
  - The context builder deliberately interleaves audit scores with
    research findings rather than presenting them in separate blocks —
    this mirrors how a human analyst would reason ("the value_comm score
    is 4/10 AND competitor data shows a 40% price gap — these two facts
    together make pricing the #1 priority").
"""

from __future__ import annotations

import logging
from pathlib import Path

from models import (
    AuditScores,
    DealAudit,
    OptimizationProposal,
    ProposalRecommendation,
    ResearchData,
    ResearchSynthesis,
)
from ai.client import call_with_tool, TokenUsage
from ai.schemas.proposal_schema import PROPOSAL_TOOL_NAME, PROPOSAL_TOOL_SCHEMA

log = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent.parent / "config" / "prompts" / "proposal_generation.txt"

# Larger thinking budget than the audit analyzer — generating concrete copy
# and ordering 8-10 recommendations by impact requires more reasoning.
THINKING_BUDGET = 16_000


def generate_proposal(
    audit: DealAudit,
    scores: AuditScores,
    research: ResearchData,
    synthesis: ResearchSynthesis,
) -> tuple[OptimizationProposal, TokenUsage]:
    """
    Generate an optimization proposal for a deal.

    Returns (OptimizationProposal, TokenUsage).
    Raises RuntimeError if the AI call fails after retries.
    """
    system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")
    user_message = _build_proposal_context(audit, scores, research, synthesis)

    log.info(
        "Running proposal AI for deal %s (%s) — overall=%.1f quality=%s",
        audit.deal_id,
        audit.title,
        scores.overall_score,
        synthesis.deal_quality,
    )

    result = call_with_tool(
        messages=[{"role": "user", "content": user_message}],
        tool_name=PROPOSAL_TOOL_NAME,
        tool_schema=PROPOSAL_TOOL_SCHEMA,
        system=system_prompt,
        max_tokens=8192,
        thinking_budget=THINKING_BUDGET,
    )

    d = result.data

    recommendations = [
        ProposalRecommendation(
            priority_rank=r["priority_rank"],
            category=r["category"],
            recommendation=r["recommendation"],
            current_state=r["current_state"],
            proposed_state=r["proposed_state"],
            data_citation=r["data_citation"],
            expected_impact=r["expected_impact"],
            impact_rationale=r["impact_rationale"],
        )
        for r in sorted(d["recommendations"], key=lambda x: x["priority_rank"])
    ]

    proposal = OptimizationProposal(
        deal_id=audit.deal_id,
        proposed_title=d["proposed_title"],
        proposed_meta_title=d["proposed_meta_title"],
        proposed_meta_description=d["proposed_meta_description"],
        proposed_h1=d["proposed_h1"],
        proposed_highlights=d["proposed_highlights"],
        pricing_framing=d["pricing_framing"],
        executive_summary=d["executive_summary"],
        recommendations=recommendations,
    )

    log.info(
        "Proposal generated for %s: %d recommendations, tokens=%d",
        audit.deal_id,
        len(recommendations),
        result.usage.total,
    )

    return proposal, result.usage


def _build_proposal_context(
    audit: DealAudit,
    scores: AuditScores,
    research: ResearchData,
    synthesis: ResearchSynthesis,
) -> str:
    """
    Build the synthesis context for the proposal prompt.

    Structure: deal identity → current page state (scored) → research
    findings → AI synthesis verdicts → explicit instruction.

    Interleaving scores with facts keeps the AI from treating them as
    separate silos and produces better cross-referencing in citations.
    """
    p = audit.primary_pricing
    groupon_price = f"${p.deal_price:.2f}" if p and p.deal_price else "unknown"
    original_price = f"${p.original_price:.2f}" if p and p.original_price else "unknown"
    discount = f"{p.discount_pct:.0f}%" if p and p.discount_pct else "unknown"

    # ── Section 1: Deal identity ──────────────────────────────────────────────
    header = f"""DEAL BEING OPTIMIZED
====================
Merchant: {audit.merchant_name or 'Unknown'}
Title (current): {audit.title or 'NOT FOUND'}
Subtitle (current): {audit.subtitle or 'None'}
Category: {audit.category or 'Unknown'}
Location: {', '.join(filter(None, [audit.city, audit.state])) or 'Unknown'}
Groupon Price: {groupon_price} (claimed original: {original_price}, discount: {discount})
URL: {audit.url}"""

    # ── Section 2: Audit scores with reasoning ────────────────────────────────
    score_lines = [
        "AUDIT SCORES (1–10, WEIGHTED)",
        "==============================",
        f"Overall: {scores.overall_score:.1f}/10",
        f"  Value Communication (25% weight): {scores.value_comm_score}/10",
        f"  Completeness        (25% weight): {scores.completeness_score}/10",
        f"  Clarity             (20% weight): {scores.clarity_score}/10",
        f"  Trust               (15% weight): {scores.trust_score}/10",
        f"  SEO                 (10% weight): {scores.seo_score}/10",
        f"  Urgency              (5% weight): {scores.urgency_score}/10",
    ]
    if scores.score_reasoning:
        score_lines.append("\nScore reasoning:")
        for dim, reason in scores.score_reasoning.items():
            score_lines.append(f"  {dim}: {reason}")

    # ── Section 3: Content gaps ───────────────────────────────────────────────
    if scores.content_gaps:
        gap_lines = ["CONTENT GAPS (identified by audit AI)", "=" * 40]
        for i, gap in enumerate(scores.content_gaps, 1):
            gap_lines.append(f"  {i}. {gap}")
        gap_block = "\n".join(gap_lines)
    else:
        gap_block = "CONTENT GAPS\n============\nNone identified."

    # ── Section 4: AI flags ───────────────────────────────────────────────────
    if scores.ai_flags:
        flag_lines = ["AI FLAGS (potential trust/accuracy issues)", "=" * 40]
        for flag in scores.ai_flags:
            flag_lines.append(f"  ⚠ {flag}")
        flag_block = "\n".join(flag_lines)
    else:
        flag_block = "AI FLAGS\n========\nNone."

    # ── Section 5: Current page copy ─────────────────────────────────────────
    highlights_block = (
        "\n".join(f"  • {h}" for h in audit.highlights)
        if audit.highlights else "  (none extracted)"
    )
    fine_print_block = (
        "\n".join(f"  • {fp}" for fp in audit.fine_print[:8])
        if audit.fine_print else "  (none extracted)"
    )

    seo = audit.seo
    seo_block = f"""  Meta title ({len(seo.meta_title or '')} chars): {seo.meta_title or 'MISSING'}
  Meta description ({len(seo.meta_description or '')} chars): {seo.meta_description or 'MISSING'}
  H1: {seo.h1_text or 'MISSING'}
  H2 headings: {'; '.join(seo.h2_texts[:4]) or 'none'}
  Schema markup: {'Yes (' + (seo.schema_type or 'type unknown') + ')' if seo.has_schema_markup else 'NO'}"""

    trust_block = (
        "\n".join(f"  [{ts.signal_type}] {ts.signal_value}" for ts in audit.trust_signals)
        or "  None detected"
    )

    groupon_reviews = (
        f"  Groupon: {audit.reviews_avg_rating}/5.0 ({audit.reviews_count} reviews)"
        if audit.reviews_count else "  Groupon: not shown"
    )

    # ── Section 6: Research findings ──────────────────────────────────────────
    comp_lines = ["COMPETITOR PRICING", "=" * 40]
    if research.competitors:
        for c in research.competitors:
            price = f"${c.regular_price:.2f}" if c.regular_price else "unknown"
            if c.sale_price:
                price += f" (sale: ${c.sale_price:.2f})"
            comp_lines.append(f"  {c.competitor_name}: {price} — {c.source_url}")
        prices = [c.regular_price for c in research.competitors if c.regular_price]
        if prices:
            avg = sum(prices) / len(prices)
            comp_lines.append(f"  Competitor average: ${avg:.2f} ({len(prices)} sources)")
    else:
        comp_lines.append("  No competitor data collected.")
    comp_block = "\n".join(comp_lines)

    rep_lines = ["MERCHANT REPUTATION", "=" * 40]
    if research.yelp:
        y = research.yelp
        rep_lines.append(
            f"  Yelp: {y.rating}/5.0 ({y.review_count} reviews) — {y.url}"
        )
    if research.google:
        g = research.google
        rep_lines.append(
            f"  Google: {g.rating}/5.0 ({g.review_count} reviews)"
        )
    rep_lines.append(groupon_reviews)
    rep_block = "\n".join(rep_lines)

    # ── Section 7: Review themes ───────────────────────────────────────────────
    theme_lines = [
        "REVIEW THEMES (AI-coded from Yelp/Google reviews)",
        "=" * 50,
        "Use these themes to rewrite highlights and identify copy opportunities.",
    ]
    if synthesis.review_themes:
        for theme in sorted(synthesis.review_themes, key=lambda t: -t.mention_count):
            icon = "✅" if theme.sentiment == "positive" else ("❌" if theme.sentiment == "negative" else "➖")
            theme_lines.append(
                f"  {icon} [{theme.mention_count}x] \"{theme.theme}\" — "
                f"sample: \"{theme.sample_quote[:120]}\""
            )
    else:
        theme_lines.append("  No themes coded.")
    theme_block = "\n".join(theme_lines)

    # ── Section 8: Research synthesis verdict ─────────────────────────────────
    verdict_lines = [
        "RESEARCH SYNTHESIS VERDICT",
        "=" * 40,
        f"Deal quality: {synthesis.deal_quality.upper()}",
        f"Value assessment: {synthesis.value_assessment}",
        f"Value reasoning: {synthesis.value_reasoning}",
    ]
    if synthesis.groupon_vs_direct_savings is not None:
        verdict_lines.append(f"Savings vs. direct booking: ${synthesis.groupon_vs_direct_savings:.2f}")
    if synthesis.groupon_vs_competitor_savings is not None:
        verdict_lines.append(f"Savings vs. competitor average: ${synthesis.groupon_vs_competitor_savings:.2f}")
    if synthesis.merchant_differentiators:
        verdict_lines.append("\nMerchant differentiators:")
        for diff in synthesis.merchant_differentiators:
            verdict_lines.append(f"  • {diff}")
    if synthesis.red_flags:
        verdict_lines.append("\nRed flags from research:")
        for flag in synthesis.red_flags:
            verdict_lines.append(f"  ⚠ {flag}")
    verdict_block = "\n".join(verdict_lines)

    # ── Section 9: Category context ───────────────────────────────────────────
    if research.category_context:
        ctx = research.category_context
        cat_block = (
            f"CATEGORY CONTEXT\n{'='*40}\n"
            f"  Category: {ctx.category} in {ctx.city}\n"
        )
        if ctx.typical_price_low and ctx.typical_price_high:
            cat_block += f"  Typical market range: ${ctx.typical_price_low:.0f}–${ctx.typical_price_high:.0f}\n"
        if ctx.typical_discount_pct:
            cat_block += f"  Typical Groupon discount in this category: {ctx.typical_discount_pct:.0f}%\n"
        if ctx.notes:
            cat_block += f"  Notes: {ctx.notes}\n"
    else:
        cat_block = ""

    # ── Assemble ──────────────────────────────────────────────────────────────
    current_page = f"""CURRENT DEAL PAGE CONTENT
==========================
Highlights:
{highlights_block}

Fine print (key items):
{fine_print_block}

SEO:
{seo_block}

Trust signals:
{trust_block}"""

    closing = (
        "Using all of the above data, generate a complete optimization proposal "
        "using the generate_optimization_proposal tool. "
        "Proposed copy must be ready to publish — write the actual words, "
        "not a description of what to write. "
        "Every recommendation must cite a specific data point from the above."
    )

    sections = [
        header,
        "\n".join(score_lines),
        gap_block,
        flag_block,
        current_page,
        comp_block,
        rep_block,
        theme_block,
        verdict_block,
    ]
    if cat_block:
        sections.append(cat_block)
    sections.append(closing)

    return "\n\n".join(sections)
