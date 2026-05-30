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

import json as _json
import re
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
        "good":    "🟢 GOOD",
        "average": "🟡 AVERAGE",
        "weak":    "🔴 WEAK",
    }.get(quality, f"❓ {quality.upper()}")

    lines += [
        f"# Optimization Proposal: {a.title or 'Untitled Deal'}",
        "",
        f"**Merchant:** {a.merchant_name or '—'}  ",
        f"**Location:** {_resolve_location(a) or '—'}"
        + (
            f" ({_addr})"
            if (_addr := _validated_address(getattr(a, 'redemption_address', None)))
            and _addr != _resolve_location(a)
            else ""
        )
        + "  ",
        f"**Category:** {a.category or '—'}  ",
        f"**Deal Quality:** {quality_badge}  ",
        f"**Deal ID:** `{a.deal_id}`  ",
        "",
    ]
    if scores:
        deal_score = synthesis.deal_quality_score if synthesis else None
        lines += [
            f"**Page quality score:** {scores.overall_score:.1f}/10  ",
        ]
        if deal_score is not None:
            lines.append(f"**Deal quality score:** {deal_score:.1f}/10  ")
        lines.append("")
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

    # ── Image Recommendations (Improvement 5) ───────────────────────────────
    if p.image_recommendations:
        lines += ["## Image Recommendations", ""]
        lines += [
            "_Specific images that would improve purchase confidence for this deal._",
            "",
            "| Priority | Image to Add / Change | Why It Helps Conversion |",
            "|:--------:|----------------------|------------------------|",
        ]
        priority_icon = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}
        for ir in p.image_recommendations:
            icon = priority_icon.get(ir.priority, "⚪")
            lines.append(
                f"| {icon} {ir.priority} | {ir.image_type} | {ir.conversion_reason} |"
            )
        lines += ["", "---", ""]

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
        score_str = f" (impact score: {rec.impact_score})" if rec.impact_score else ""
        # Use short_title in the section header (clean, no truncation mid-word)
        header_label = rec.short_title or rec.recommendation
        lines += [
            f"### #{rec.priority_rank} — {impact_icon} {cat_label}: {header_label}",
            "",
            f"**Expected impact:** {impact_icon} {rec.expected_impact.capitalize()}{score_str}",
            "",
            f"**Why it matters:** {rec.impact_rationale}",
            "",
            f"**Data citation:** _{rec.data_citation}_",
            "",
        ]
        # Supporting evidence (Priority 5)
        if rec.supporting_evidence:
            lines += ["**Evidence:**", ""]
            for ev in rec.supporting_evidence:
                lines.append(f"- {ev}")
            lines.append("")
        lines += [
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
        table_label = rec.short_title or rec.recommendation
        lines.append(
            f"| {rec.priority_rank} | {rec.category} "
            f"| {table_label} "
            f"| 🔴 High |"
        )
    if med_recs:
        lines.append("| — | | _Medium-impact items below_ | |")
        for rec in med_recs[:3]:
            table_label = rec.short_title or rec.recommendation
            lines.append(
                f"| {rec.priority_rank} | {rec.category} "
                f"| {table_label} "
                f"| 🟡 Medium |"
            )
    lines.append("")

    # ── Coverage check ───────────────────────────────────────────────────────
    # A dimension is only "not addressed" if it has ZERO score AND ZERO
    # reasoning AND no mention anywhere in the proposal body (no recommendation
    # and no dedicated rendered section). A dimension that carries a score but
    # simply needs no dedicated fix (e.g. urgency 6/10) must stay silent — it is
    # NOT unaddressed.
    covered_cats = {r.category for r in p.recommendations}

    # The "Proposed Rewritten Copy" block always renders these, so they are
    # addressed whenever they carry content.
    if p.proposed_title:
        covered_cats.add("title")
    if p.proposed_meta_title or p.proposed_meta_description or p.proposed_h1:
        covered_cats.add("seo")
    if p.proposed_highlights:
        covered_cats.add("highlights")
    if (p.pricing_framing or "").strip():
        covered_cats.add("pricing")
    # The Image Recommendations table renders from image_recommendations.
    if p.image_recommendations:
        covered_cats.add("images")

    # Any dimension that the audit scored (or wrote reasoning for) has content
    # in the report and must not be flagged as unaddressed.
    if scores:
        reasoning = scores.score_reasoning or {}
        # footer dimension → (audit score attribute, score_reasoning key)
        scored_dims = {
            "trust": (scores.trust_score, "trust"),
            "urgency": (scores.urgency_score, "urgency"),
            "seo": (scores.seo_score, "seo"),
            "pricing": (scores.value_comm_score, "value_comm"),
            "content": (scores.completeness_score, "completeness"),
        }
        for cat, (score_val, reason_key) in scored_dims.items():
            if (score_val and score_val > 0) or reasoning.get(reason_key):
                covered_cats.add(cat)

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


def _resolve_location(a: DealAudit) -> Optional[str]:
    """
    Return location string, falling back to audit JSON if city/state are missing.
    Validates extracted values so page-copy fragments never leak into the header.
    """
    loc = _location(_validated_city(a.city), a.state)
    if loc:
        return loc
    # Fallback: AI-inferred location label (e.g. "Online", "Nationwide").
    if getattr(a, "location_label", None):
        return a.location_label
    try:
        audit_json = settings.outputs_dir / a.deal_id / "1_audit" / "audit.json"
        if audit_json.exists():
            data = _json.loads(audit_json.read_text(encoding="utf-8"))
            loc = _location(_validated_city(data.get("city")), data.get("state"))
            return loc or data.get("location_label") or None
    except Exception:
        pass
    return None


def _validated_address(addr: Optional[str]) -> Optional[str]:
    """
    Return the redemption address only if it looks like a real street address.

    The scraper can occasionally capture page copy (e.g. course terms or review
    text) in the redemption field. We never want that leaking into the header,
    so we require a leading street number, a comma, a recognisable street-type
    token, and the absence of sentence-fragment signals.
    """
    if not addr:
        return None
    addr = addr.strip()
    if len(addr) > 80 or "\n" in addr or "," not in addr:
        return None
    if not re.match(r"^\d", addr):
        return None
    _NOT_ADDR = re.compile(
        r"\b(month|week|year|course|enrollment|starting|voucher|valid|expires?|"
        r"redeem|purchase|review|ago|helpful|reviewed|getaway|grandchildren)\b",
        re.IGNORECASE,
    )
    if _NOT_ADDR.search(addr):
        return None
    _STREET_TOKEN = re.compile(
        r"\b(Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Way|"
        r"Court|Ct|Place|Pl|Parkway|Pkwy|Highway|Hwy|Square|Sq|Terrace|Ter|Suite|Ste|Unit)\b",
        re.IGNORECASE,
    )
    if not _STREET_TOKEN.search(addr):
        return None
    return addr


def _validated_city(city: Optional[str]) -> Optional[str]:
    """Return city only if it looks like a real place name, else None."""
    if not city:
        return None
    _NOT_CITY = re.compile(
        r"\b(month|week|day|year|course|enrollment|starting|from your|per|"
        r"purchase|voucher|valid|expires?|redeemable|redeem|subject|prior|"
        r"book|appointment|advance|required|included|exclud|limit|maximum|"
        r"minimum|must|cannot|please|contact|call|visit|online|http|www)\b",
        re.IGNORECASE,
    )
    if len(city) > 40 or _NOT_CITY.search(city):
        return None
    if not re.match(r"^[A-Za-z][A-Za-z\s\-\'\.]{0,39}$", city):
        return None
    return city
