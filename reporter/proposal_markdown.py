"""
Proposal markdown reporter — renders a CEO-ready optimization proposal.

Produces: outputs/{deal_id}/3_proposal/optimization_proposal.md

Sections:
  1. Executive Summary (deal identity + quality verdict + summary paragraph)
  2. Rewritten Copy (proposed title, meta, H1, highlights, pricing framing)
  3. Before/After comparison for title and meta
  4. Priority-Ranked Recommendations table (full detail)
  5. Quick wins — high-impact items only
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from models import AuditScores, DealAudit, OptimizationProposal, ResearchSynthesis
from config.settings import settings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def write_proposal_markdown(
    deal_id: str,
    audit: DealAudit,
    proposal: OptimizationProposal,
    scores: Optional[AuditScores] = None,
    synthesis: Optional[ResearchSynthesis] = None,
) -> Path:
    """Render optimization_proposal.md and return its path."""
    out_dir = settings.outputs_dir / deal_id / "3_proposal"
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / "optimization_proposal.md"
    out_path.write_text(
        _render(audit, proposal, scores, synthesis), encoding="utf-8"
    )
    return out_path


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

def _render(
    a: DealAudit,
    p: OptimizationProposal,
    scores: Optional[AuditScores],
    synthesis: Optional[ResearchSynthesis],
) -> str:
    lines: list[str] = []

    # ── Header ──────────────────────────────────────────────────────────────
    quality = synthesis.deal_quality if synthesis else "unknown"
    quality_badge = {
        "strong":  "🟢 STRONG",
        "average": "🟡 AVERAGE",
        "weak":    "🔴 WEAK",
    }.get(quality, f"❓ {quality.upper()}")

    lines += [
        f"# Optimization Proposal: {a.title or 'Untitled Deal'}",
        "",
        f"**Merchant:** {a.merchant_name or '—'}  ",
        f"**Location:** {_location(a.city, a.state) or '—'}  ",
        f"**Category:** {a.category or '—'}  ",
        f"**Deal Quality:** {quality_badge}  ",
        f"**Deal ID:** `{a.deal_id}`  ",
        "",
    ]
    if scores:
        lines += [
            f"**Current overall score:** {scores.overall_score:.1f}/10  ",
            "",
        ]
    lines += ["---", ""]

    # ── Executive Summary ────────────────────────────────────────────────────
    lines += [
        "## Executive Summary",
        "",
        p.executive_summary,
        "",
        "---",
        "",
    ]

    # ── Score Snapshot ───────────────────────────────────────────────────────
    if scores:
        lines += ["## Current Page Score Snapshot", ""]
        lines += [
            "| Dimension | Score | Weight |",
            "|-----------|:-----:|:------:|",
            f"| Value Communication | {scores.value_comm_score}/10 | 25% |",
            f"| Completeness | {scores.completeness_score}/10 | 25% |",
            f"| Clarity | {scores.clarity_score}/10 | 20% |",
            f"| Trust | {scores.trust_score}/10 | 15% |",
            f"| SEO | {scores.seo_score}/10 | 10% |",
            f"| Urgency | {scores.urgency_score}/10 | 5% |",
            f"| **Overall** | **{scores.overall_score:.1f}/10** | — |",
        ]
        lines.append("")

        if scores.content_gaps:
            lines += ["**Key content gaps:**", ""]
            for gap in scores.content_gaps:
                lines.append(f"- {gap}")
            lines.append("")

        lines += ["---", ""]

    # ── Rewritten Copy ───────────────────────────────────────────────────────
    lines += ["## Proposed Rewritten Copy", ""]

    # Before/after title
    lines += ["### Title", ""]
    lines += [
        "| | Text |",
        "|---|------|",
        f"| **Current** | {a.title or '_missing_'} |",
        f"| **Proposed** | **{p.proposed_title}** |",
        "",
    ]

    # Before/after meta title
    lines += ["### Meta Title (SEO)", ""]
    current_meta_title = a.seo.meta_title or "_missing_"
    current_meta_len = len(a.seo.meta_title or "")
    proposed_meta_len = len(p.proposed_meta_title)
    lines += [
        "| | Text | Length |",
        "|---|------|:------:|",
        f"| **Current** | {current_meta_title} | {current_meta_len} chars |",
        f"| **Proposed** | **{p.proposed_meta_title}** | {proposed_meta_len} chars |",
        "",
    ]
    if proposed_meta_len > 60:
        lines += [f"> ⚠️ Proposed meta title is {proposed_meta_len} chars — aim for ≤60.", ""]

    # Before/after meta description
    lines += ["### Meta Description (SEO)", ""]
    current_meta_desc = a.seo.meta_description or "_missing_"
    current_meta_desc_len = len(a.seo.meta_description or "")
    proposed_meta_desc_len = len(p.proposed_meta_description)
    lines += [
        "| | Text | Length |",
        "|---|------|:------:|",
        f"| **Current** | {current_meta_desc[:120]}{'…' if len(current_meta_desc) > 120 else ''} | {current_meta_desc_len} chars |",
        f"| **Proposed** | **{p.proposed_meta_description}** | {proposed_meta_desc_len} chars |",
        "",
    ]
    if proposed_meta_desc_len > 155:
        lines += [f"> ⚠️ Proposed meta description is {proposed_meta_desc_len} chars — aim for ≤155.", ""]

    # H1
    lines += ["### H1 Heading", ""]
    lines += [
        "| | Text |",
        "|---|------|",
        f"| **Current** | {a.seo.h1_text or '_missing_'} |",
        f"| **Proposed** | **{p.proposed_h1}** |",
        "",
    ]

    # Proposed highlights
    lines += ["### Highlights / What You Get (Full Rewrite)", ""]
    lines += ["**Proposed bullets:**", ""]
    for bullet in p.proposed_highlights:
        lines.append(f"- {bullet}")
    lines.append("")
    if a.highlights:
        lines += [
            "<details>",
            "<summary>Current highlights (click to expand)</summary>",
            "",
        ]
        for h in a.highlights:
            lines.append(f"- {h}")
        lines += ["", "</details>", ""]

    # Pricing framing
    lines += ["### Pricing & Value Framing", ""]
    lines += [p.pricing_framing, "", "---", ""]

    # ── Ranked Recommendations ───────────────────────────────────────────────
    lines += ["## Priority-Ranked Recommendations", ""]
    lines += [
        "_Ordered by expected conversion impact. Implement top items first._",
        "",
    ]

    high_recs = [r for r in p.recommendations if r.expected_impact == "high"]
    med_recs  = [r for r in p.recommendations if r.expected_impact == "medium"]
    low_recs  = [r for r in p.recommendations if r.expected_impact == "low"]

    for rec in p.recommendations:
        impact_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(
            rec.expected_impact, "⚪"
        )
        cat_label = rec.category.upper()
        lines += [
            f"### #{rec.priority_rank} — {impact_icon} {cat_label}: {rec.recommendation[:80]}",
            "",
            f"**Expected impact:** {impact_icon} {rec.expected_impact.capitalize()}",
            "",
            f"**Why it matters:** {rec.impact_rationale}",
            "",
            f"**Data citation:** _{rec.data_citation}_",
            "",
            "| | |",
            "|---|---|",
            f"| **Current state** | {rec.current_state} |",
            f"| **Proposed state** | {rec.proposed_state} |",
            "",
        ]

    lines += ["---", ""]

    # ── Quick Wins Summary Table ─────────────────────────────────────────────
    lines += ["## Quick Wins Summary", ""]
    lines += [
        "_High-impact changes only. Ship these first._",
        "",
        "| # | Category | Change | Impact |",
        "|---|----------|--------|:------:|",
    ]
    for rec in high_recs:
        lines.append(
            f"| {rec.priority_rank} | {rec.category} "
            f"| {rec.recommendation[:80]}{'…' if len(rec.recommendation) > 80 else ''} "
            f"| 🔴 High |"
        )
    if med_recs:
        lines.append("| — | | _Medium-impact items below_ | |")
        for rec in med_recs[:3]:
            lines.append(
                f"| {rec.priority_rank} | {rec.category} "
                f"| {rec.recommendation[:80]}{'…' if len(rec.recommendation) > 80 else ''} "
                f"| 🟡 Medium |"
            )
    lines.append("")

    # ── Coverage check ───────────────────────────────────────────────────────
    covered_cats = {r.category for r in p.recommendations}
    all_cats = {"title", "pricing", "highlights", "content", "images", "seo", "positioning", "trust", "urgency"}
    uncovered = all_cats - covered_cats
    if uncovered:
        lines += [
            "---",
            "",
            f"_Note: The following page dimensions were not addressed in this proposal: "
            f"{', '.join(sorted(uncovered))}. This may indicate no significant issues were "
            f"found in those areas._",
            "",
        ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _location(city, state) -> Optional[str]:
    parts = [p for p in (city, state) if p]
    return ", ".join(parts) if parts else None
