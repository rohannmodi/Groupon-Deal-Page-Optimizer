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

import json as _json
import logging
from pathlib import Path
from typing import Any

from models import (
    AuditScores,
    DealAudit,
    ImageRecommendation,
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


def _clean_short_title(short_title: str, recommendation: str) -> str:
    """
    Ensure short_title is at most 10 words and ends at a word boundary (no ellipsis).
    Falls back to truncating the recommendation if short_title is empty.
    """
    text = short_title.strip() or recommendation.strip()
    words = text.split()
    if len(words) <= 10:
        return text
    # Truncate at 10 words, clean trailing punctuation
    truncated = " ".join(words[:10]).rstrip(".,;:—–-")
    return truncated


import re as _re

# Matches an advertised savings claim tied to buying items separately, e.g.
# "Save $60 versus buying separately" or "$60 in savings vs buying separately".
_BUNDLE_SAVINGS_RE = _re.compile(
    r"\$?\s?(\d+(?:\.\d{1,2})?)[^.\n]{0,40}?(?:separate|individually|on their own)",
    _re.IGNORECASE,
)
_BUNDLE_NAME_RE = _re.compile(r"\bbundle\b|\bboth\b|\d\s*-?\s*course|\&|\+|combo", _re.IGNORECASE)


def _detect_bundle_savings_issue(audit: DealAudit) -> dict | None:
    """
    Deterministically detect a bundle whose advertised savings is contradicted
    by the actual pricing options (e.g. "Save $60" when the bundle costs the
    same as — or more than — buying each option separately).

    Returns a recommendation dict (ready to merge into the AI recommendation
    list) when a material discrepancy is found, otherwise None.

    This runs independently of the AI so a genuine pricing-integrity issue is
    never dropped just because the model failed to surface it.
    """
    options = [o for o in audit.pricing_options if o.deal_price]
    if len(options) < 2:
        return None

    bundles = [o for o in options if _BUNDLE_NAME_RE.search(o.option_name or "")]
    singles = [o for o in options if o not in bundles]
    if not bundles or len(singles) < 2:
        return None

    # Cost of buying the individual components separately = sum of the distinct
    # single-option prices (the common 2-course / 2-item bundle case).
    separately_cost = round(sum(o.deal_price for o in singles), 2)
    bundle = min(bundles, key=lambda o: o.deal_price)
    actual_savings = round(separately_cost - bundle.deal_price, 2)

    # Find the advertised savings claim in the page copy.
    haystacks = [audit.description or ""]
    haystacks += list(audit.highlights or [])
    haystacks += list(audit.fine_print or [])
    advertised: float | None = None
    claim_text = ""
    for text in haystacks:
        m = _BUNDLE_SAVINGS_RE.search(text)
        if m:
            advertised = float(m.group(1))
            # Capture a short, readable snippet around the claim.
            start = max(0, m.start() - 30)
            claim_text = " ".join(text[start:m.end() + 10].split())
            break

    if advertised is None:
        return None

    # Only flag a *material* contradiction: the claim overstates real savings
    # by a wide margin (and the real savings is negligible or negative).
    if not (advertised - actual_savings >= 5 and actual_savings <= max(1.0, advertised * 0.1)):
        return None

    singles_desc = " + ".join(
        f"${o.deal_price:.2f} ({(o.option_name or 'option').strip()[:40]})" for o in singles
    )
    if actual_savings < 0:
        actual_phrase = f"the bundle costs ${abs(actual_savings):.2f} MORE than buying separately"
    elif actual_savings == 0:
        actual_phrase = "the bundle costs exactly the same as buying separately"
    else:
        actual_phrase = f"the real savings is only ${actual_savings:.2f}"

    return {
        "priority_rank": 1,
        "category": "pricing",
        "short_title": "Fix false bundle savings claim",
        "recommendation": (
            f"Correct or remove the advertised \"save ${advertised:.0f}\" bundle claim. "
            f"The bundle is priced at ${bundle.deal_price:.2f}, while buying the options "
            f"separately costs ${separately_cost:.2f} ({singles_desc}) — "
            f"{actual_phrase}. Either reprice the bundle so it delivers the promised "
            f"savings, or replace the claim with the accurate figure."
        ),
        "current_state": (
            f'Page advertises "{claim_text}" but the bundle ${bundle.deal_price:.2f} vs. '
            f"${separately_cost:.2f} separately means {actual_phrase}."
        ),
        "proposed_state": (
            "Remove the unsubstantiated savings figure. Either reprice the bundle below "
            f"${separately_cost:.2f} and state the verified savings, or frame the bundle on "
            "convenience (one purchase, both certifications) rather than a price discount."
        ),
        "data_citation": (
            f"pricing_options: bundle ${bundle.deal_price:.2f} vs. separately ${separately_cost:.2f}; "
            f'advertised claim "{claim_text}"'
        ),
        "expected_impact": "high",
        "impact_rationale": (
            "A demonstrably false savings claim is a trust and compliance risk that "
            "undermines every other value message on the page."
        ),
        "supporting_evidence": [
            f"Advertised savings: ${advertised:.0f}",
            f"Actual savings: ${actual_savings:.2f} (bundle ${bundle.deal_price:.2f} vs. ${separately_cost:.2f} separately)",
            f"Pricing options on page: {len(options)}",
        ],
        # High score so it sorts to (or near) the top of the priority list.
        "impact_score": 900,
    }


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

    # Defensive parsing: with extended thinking + tool_choice:auto, the API
    # occasionally serialises recommendation objects as JSON strings instead of
    # dicts. Detect and fix that before indexing into them.
    raw_recs: list[Any] = d.get("recommendations", [])
    parsed_recs: list[dict] = []
    for item in raw_recs:
        if isinstance(item, dict):
            parsed_recs.append(item)
        elif isinstance(item, str):
            # Try to parse a JSON-encoded recommendation object
            try:
                obj = _json.loads(item)
                if isinstance(obj, dict):
                    parsed_recs.append(obj)
                    log.warning("Recommendation was JSON string — parsed successfully")
            except Exception:
                log.warning("Skipping unparseable recommendation string: %r", item[:80])
        else:
            log.warning("Unexpected recommendation type %s — skipping", type(item))

    # Deterministic safety net: surface a high-priority pricing recommendation
    # when an advertised bundle "savings" claim is contradicted by the actual
    # pricing options. This does not depend on the AI noticing the discrepancy.
    bundle_rec = _detect_bundle_savings_issue(audit)
    if bundle_rec is not None:
        # Avoid duplicating if the AI already flagged the same bundle issue.
        if not any(
            r.get("category") == "pricing"
            and "bundle" in (r.get("recommendation", "") + r.get("data_citation", "")).lower()
            for r in parsed_recs
        ):
            parsed_recs.append(bundle_rec)

    # Sort by impact_score DESC (Priority 6), fallback to priority_rank
    sorted_recs = sorted(
        parsed_recs,
        key=lambda x: (-x.get("impact_score", 0), x.get("priority_rank") or 999)
    )
    # Re-assign priority_rank based on impact_score ordering.
    #
    # NOTE: We do NOT filter on the model-supplied priority_rank here. priority_rank
    # is re-derived from the impact_score ordering below, so a missing/None value
    # from the model is harmless. A previous `if r.get("priority_rank") is not None`
    # guard silently discarded EVERY recommendation whenever the model omitted that
    # field — producing an empty Priority-Ranked section. A recommendation is kept
    # as long as it has the substantive content (a recommendation statement).
    recommendations = [
        ProposalRecommendation(
            priority_rank=i + 1,
            category=r.get("category", "content"),
            short_title=_clean_short_title(r.get("short_title", ""), r.get("recommendation", "")),
            recommendation=r.get("recommendation", ""),
            current_state=r.get("current_state", ""),
            proposed_state=r.get("proposed_state", ""),
            data_citation=r.get("data_citation", ""),
            expected_impact=r.get("expected_impact", "medium"),
            impact_rationale=r.get("impact_rationale", ""),
            supporting_evidence=r.get("supporting_evidence", []),
            impact_score=r.get("impact_score", 0),
        )
        for i, r in enumerate(sorted_recs)
        if (r.get("recommendation") or "").strip()
    ]

    # Parse image recommendations (Improvement 5)
    raw_img_recs = d.get("image_recommendations", [])
    image_recommendations = [
        ImageRecommendation(
            image_type=ir.get("image_type", ""),
            conversion_reason=ir.get("conversion_reason", ""),
            priority=ir.get("priority", "Medium"),
        )
        for ir in raw_img_recs
        if isinstance(ir, dict) and ir.get("image_type")
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
        image_recommendations=image_recommendations,
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
Location: {', '.join(filter(None, [audit.city, audit.state])) or 'Unknown'}{f" | Address on page: {audit.redemption_address}" if getattr(audit, 'redemption_address', None) else ""}
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
        f"Deal quality label: {synthesis.deal_quality.upper()}",
        f"Deal quality score: {synthesis.deal_quality_score}/10 (deal price + merchant + demand)",
        f"Page quality score: {scores.overall_score:.1f}/10 (audit weighted score)",
        f"Value assessment: {synthesis.value_assessment}",
        f"Value reasoning: {synthesis.value_reasoning}",
    ]
    if synthesis.key_insight:
        verdict_lines.insert(3, f"KEY INSIGHT: {synthesis.key_insight}")
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

    # ── Section 9a: Stale content (Bug 3) ───────────────────────────────────
    stale_warnings = getattr(audit, "stale_content_warnings", [])
    if stale_warnings:
        stale_lines = [
            "STALE CONTENT — HIGH PRIORITY FIX",
            "=" * 40,
            "The following outdated text is ACTIVE on the live page right now.",
            "Generate a recommendation with category='content' and expected_impact='high'",
            "for removal of this content.",
        ]
        for w in stale_warnings:
            stale_lines.append(
                f"  [{w.get('location', '?')}] {w.get('signal', '')}: "
                f'"{w.get("text", "")[:120]}"'
            )

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
    if stale_warnings:
        sections.append("\n".join(stale_lines))
    if cat_block:
        sections.append(cat_block)
    sections.append(closing)

    return "\n\n".join(sections)
