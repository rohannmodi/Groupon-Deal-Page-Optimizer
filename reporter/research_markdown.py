"""
Research markdown reporter — renders a human-readable research report.

Produces: outputs/{deal_id}/2_research/research_report.md

Sections:
  1. Header (deal identity, scores summary)
  2. AI Audit Scores (scorecard table + reasoning + content gaps)
  3. Competitor Pricing (table with source links)
  4. Merchant Reputation (Yelp + Google ratings, review themes table)
  5. Value Verdict (deal quality, value_assessment, savings analysis)
  6. Red Flags
  7. Sources (cited URLs)
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from models import AuditScores, DealAudit, ResearchData, ResearchSynthesis
from config.settings import settings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def write_research_markdown(
    deal_id: str,
    audit: DealAudit,
    research: ResearchData,
    synthesis: ResearchSynthesis,
    scores: Optional[AuditScores] = None,
) -> Path:
    """Render research_report.md and return its path."""
    out_dir = settings.outputs_dir / deal_id / "2_research"
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / "research_report.md"
    out_path.write_text(_render(audit, research, synthesis, scores), encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

def _render(
    a: DealAudit,
    r: ResearchData,
    s: ResearchSynthesis,
    scores: Optional[AuditScores],
) -> str:
    lines: list[str] = []

    # ── Header ──────────────────────────────────────────────────────────────
    lines += [
        f"# Research Report: {a.title or 'Untitled Deal'}",
        "",
        f"**Merchant:** {a.merchant_name or '—'}  ",
        f"**Location:** {_location(a.city, a.state) or '—'}  ",
        f"**Category:** {a.category or '—'}  ",
        f"**Deal URL:** {a.url}  ",
        f"**Deal ID:** `{a.deal_id}`  ",
        "",
        "---",
        "",
    ]

    # ── Deal Quality Banner ──────────────────────────────────────────────────
    quality_badge = {
        "strong": "🟢 **STRONG DEAL**",
        "average": "🟡 **AVERAGE DEAL**",
        "weak": "🔴 **WEAK DEAL**",
    }.get(s.deal_quality, f"❓ {s.deal_quality.upper()}")
    value_badge = {
        "genuine_deal": "✅ Genuine Deal",
        "marginal": "⚠️ Marginal Value",
        "overpriced": "❌ Overpriced",
    }.get(s.value_assessment, s.value_assessment)

    lines += [
        "## Overall Verdict",
        "",
        f"| Deal Quality | Value Assessment |",
        f"|:---:|:---:|",
        f"| {quality_badge} | {value_badge} |",
        "",
        f"> {s.value_reasoning}",
        "",
    ]

    # Savings summary
    savings_parts = []
    if s.groupon_vs_direct_savings is not None:
        savings_parts.append(f"**${s.groupon_vs_direct_savings:,.2f}** vs. booking direct")
    if s.groupon_vs_competitor_savings is not None:
        savings_parts.append(f"**${s.groupon_vs_competitor_savings:,.2f}** vs. competitor average")
    if savings_parts:
        lines += [f"**Estimated savings:** {' | '.join(savings_parts)}", ""]

    lines += ["---", ""]

    # ── AI Audit Scores ──────────────────────────────────────────────────────
    if scores:
        lines += ["## AI Audit Scores", ""]
        lines += _scores_table(scores)
        lines.append("")

        if scores.score_reasoning:
            lines += ["**Score Reasoning:**", ""]
            for dim, reason in scores.score_reasoning.items():
                label = _score_label(dim)
                lines.append(f"- **{label}:** {reason}")
            lines.append("")

        if scores.content_gaps:
            lines += ["### Content Gaps", ""]
            lines += [
                "_These are things a cautious first-time buyer would want to know that "
                "are missing from the current deal page._",
                "",
            ]
            for i, gap in enumerate(scores.content_gaps, 1):
                lines.append(f"{i}. {gap}")
            lines.append("")

        if scores.ai_flags:
            lines += ["### ⚠️ AI Flags", ""]
            for flag in scores.ai_flags:
                lines.append(f"- {flag}")
            lines.append("")

        lines += ["---", ""]

    # ── Competitor Pricing ───────────────────────────────────────────────────
    lines += ["## Competitor Pricing", ""]
    groupon_price = _groupon_price(a)
    if groupon_price is not None:
        lines.append(f"**Groupon deal price:** ${groupon_price:,.2f}")
        lines.append("")

    if r.competitors:
        lines += [
            "| Competitor | Service | Regular Price | Sale Price | Source |",
            "|------------|---------|:---:|:---:|--------|",
        ]
        for c in r.competitors:
            reg = f"${c.regular_price:,.2f}" if c.regular_price is not None else "—"
            sale = f"${c.sale_price:,.2f}" if c.sale_price is not None else "—"
            domain = _domain(c.source_url)
            note = f" _{c.notes}_" if c.notes else ""
            lines.append(
                f"| {c.competitor_name} | {c.service_name} | {reg} | {sale} "
                f"| [{domain}]({c.source_url}) |{note}"
            )
        lines.append("")

        # Compute and display market average if we have prices
        prices = [
            c.regular_price for c in r.competitors if c.regular_price is not None
        ]
        if prices:
            avg_price = sum(prices) / len(prices)
            lines.append(f"**Competitor average (regular price):** ${avg_price:,.2f} across {len(prices)} sources")
            if groupon_price is not None:
                pct_off = (avg_price - groupon_price) / avg_price * 100
                lines.append(
                    f"**Groupon vs. market:** ${avg_price - groupon_price:,.2f} savings "
                    f"({pct_off:.0f}% below average)"
                )
        lines.append("")
    else:
        lines.append("_No competitor pricing data found._")
        lines.append("")

    lines += ["---", ""]

    # ── Merchant Reputation ──────────────────────────────────────────────────
    lines += ["## Merchant Reputation", ""]

    has_rep_data = r.yelp or r.google
    if has_rep_data:
        lines += [
            "| Platform | Rating | Reviews | Source |",
            "|----------|:------:|:-------:|--------|",
        ]
        if r.yelp:
            rating_str = f"{r.yelp.rating} ★" if r.yelp.rating else "—"
            count_str = str(r.yelp.review_count) if r.yelp.review_count else "—"
            lines.append(f"| Yelp | {rating_str} | {count_str} | [{_domain(r.yelp.url)}]({r.yelp.url}) |")
        if r.google:
            rating_str = f"{r.google.rating} ★" if r.google.rating else "—"
            count_str = str(r.google.review_count) if r.google.review_count else "—"
            src = r.google.source_url or ""
            src_link = f"[Google]({src})" if src else "Google"
            lines.append(f"| Google | {rating_str} | {count_str} | {src_link} |")
        lines.append("")

        # Groupon on-page reviews as a row too
        if a.reviews_count or a.reviews_avg_rating:
            lines += [
                "| Groupon (on-page) | "
                + (f"{a.reviews_avg_rating} ★" if a.reviews_avg_rating else "—")
                + " | "
                + (str(a.reviews_count) if a.reviews_count else "—")
                + " | — |"
            ]
            lines.append("")
    else:
        lines.append("_No external reputation data found._")
        lines.append("")

    # Review Themes
    if s.review_themes:
        lines += ["### Review Themes", ""]
        lines += [
            "| Theme | Sentiment | Mentions | Sample Quote | Platform |",
            "|-------|:---------:|:--------:|--------------|----------|",
        ]
        for theme in sorted(s.review_themes, key=lambda t: -t.mention_count):
            sentiment_icon = {"positive": "✅", "negative": "❌", "neutral": "➖"}.get(
                theme.sentiment, theme.sentiment
            )
            # Truncate quote for table readability
            quote = theme.sample_quote[:120].replace("|", "\\|")
            if len(theme.sample_quote) > 120:
                quote += "…"
            lines.append(
                f"| {theme.theme} | {sentiment_icon} {theme.sentiment} "
                f"| {theme.mention_count} | _{quote}_ | {theme.platform} |"
            )
        lines.append("")

        # Sentiment summary
        pos = sum(1 for t in s.review_themes if t.sentiment == "positive")
        neg = sum(1 for t in s.review_themes if t.sentiment == "negative")
        neu = sum(1 for t in s.review_themes if t.sentiment == "neutral")
        lines.append(
            f"_Theme breakdown: {pos} positive, {neg} negative, {neu} neutral "
            f"across {len(s.review_themes)} coded themes._"
        )
        lines.append("")

    # Sample Yelp reviews
    if r.yelp and r.yelp.reviews:
        lines += ["### Sample Yelp Reviews", ""]
        for rev in r.yelp.reviews[:3]:
            byline = f"*{rev.author or 'Anonymous'}*"
            if rev.rating:
                byline += f" — {rev.rating} ★"
            if rev.date:
                byline += f" · {rev.date}"
            lines += [f"> {rev.text[:400]}", f"> {byline}", ""]

    lines += ["---", ""]

    # ── Category Context ─────────────────────────────────────────────────────
    if r.category_context:
        ctx = r.category_context
        lines += ["## Category & Market Context", ""]

        price_range = ""
        if ctx.typical_price_low is not None and ctx.typical_price_high is not None:
            price_range = f"${ctx.typical_price_low:,.0f}–${ctx.typical_price_high:,.0f}"
        elif ctx.typical_price_low is not None:
            price_range = f"from ${ctx.typical_price_low:,.0f}"
        elif ctx.typical_price_high is not None:
            price_range = f"up to ${ctx.typical_price_high:,.0f}"

        fields = {
            "Category": ctx.category,
            "City": ctx.city,
            "Typical price range": price_range or None,
            "Typical discount in category": (
                f"{ctx.typical_discount_pct:.0f}%" if ctx.typical_discount_pct else None
            ),
        }
        for k, v in fields.items():
            if v:
                lines.append(f"- **{k}:** {v}")

        if ctx.notes:
            lines += ["", ctx.notes]
        lines.append("")
        lines += ["---", ""]

    # ── Merchant Differentiators ─────────────────────────────────────────────
    if s.merchant_differentiators:
        lines += ["## What Makes This Merchant Stand Out", ""]
        for diff in s.merchant_differentiators:
            lines.append(f"- {diff}")
        lines += ["", "---", ""]

    # ── Red Flags ────────────────────────────────────────────────────────────
    if s.red_flags:
        lines += ["## ⚠️ Red Flags", ""]
        lines += [
            "_These are specific concerns identified from the research data._", ""
        ]
        for flag in s.red_flags:
            lines.append(f"- {flag}")
        lines += ["", "---", ""]

    # ── Sources ──────────────────────────────────────────────────────────────
    if r.sources:
        lines += ["## Sources", ""]
        # Group by relevance
        by_type: dict[str, list] = {}
        for src in r.sources:
            key = src.relevance or src.source_type
            by_type.setdefault(key, []).append(src)

        for group_label, srcs in by_type.items():
            lines.append(f"**{group_label.replace('_', ' ').title()}**")
            for src in srcs:
                title = src.source_title or _domain(src.source_url)
                lines.append(f"- [{title}]({src.source_url})")
            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _groupon_price(audit: DealAudit) -> Optional[float]:
    """Return the primary deal price from the audit."""
    primary = audit.primary_pricing
    return primary.deal_price if primary else None


def _location(city: Optional[str], state: Optional[str]) -> Optional[str]:
    parts = [p for p in (city, state) if p]
    return ", ".join(parts) if parts else None


def _domain(url: str) -> str:
    """Extract hostname from a URL for display."""
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc.removeprefix("www.") or url[:40]
    except Exception:
        return url[:40]


def _scores_table(scores: AuditScores) -> list[str]:
    """Render the 6-dimension score table with colour-coded bar."""
    rows = [
        ("Value Communication", scores.value_comm_score, "25%"),
        ("Completeness", scores.completeness_score, "25%"),
        ("Clarity", scores.clarity_score, "20%"),
        ("Trust", scores.trust_score, "15%"),
        ("SEO", scores.seo_score, "10%"),
        ("Urgency", scores.urgency_score, "5%"),
    ]
    lines = [
        "| Dimension | Score | Weight | Rating |",
        "|-----------|:-----:|:------:|--------|",
    ]
    for label, score, weight in rows:
        bar = _score_bar(score)
        lines.append(f"| {label} | **{score}/10** | {weight} | {bar} |")
    lines.append(f"| **Overall (weighted)** | **{scores.overall_score:.1f}/10** | — | {_score_bar(int(scores.overall_score))} |")
    return lines


def _score_bar(score: int) -> str:
    """Return a simple emoji bar reflecting score quality."""
    if score >= 8:
        return "🟢 Excellent"
    elif score >= 6:
        return "🟡 Good"
    elif score >= 4:
        return "🟠 Needs work"
    else:
        return "🔴 Poor"


def _score_label(dim: str) -> str:
    """Convert snake_case dimension key to a readable label."""
    return dim.replace("_score", "").replace("_", " ").title()
