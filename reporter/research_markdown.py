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

import json as _json
import re

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
        f"**Location:** {_resolve_location(a) or '—'}  ",
        f"**Category:** {a.category or '—'}  ",
        f"**Deal URL:** {a.url}  ",
        f"**Deal ID:** `{a.deal_id}`  ",
        "",
        "---",
        "",
    ]

    # ── Key Insight Callout (Improvement 3) ─────────────────────────────────
    if s.key_insight:
        lines += [
            "> 💡 **KEY INSIGHT:** " + s.key_insight,
            "",
        ]

    # ── Deal Quality Banner (Improvement 1: split deal vs page quality) ──────
    quality_badge = {
        "strong": "🟢 **STRONG DEAL**",
        "good": "🟢 **GOOD DEAL**",
        "average": "🟡 **AVERAGE DEAL**",
        "weak": "🔴 **WEAK DEAL**",
    }.get(s.deal_quality, f"❓ {s.deal_quality.upper()}")
    value_badge = {
        "genuine_deal": "✅ Genuine Deal",
        "marginal": "⚠️ Marginal Value",
        "overpriced": "❌ Overpriced",
    }.get(s.value_assessment, s.value_assessment)
    page_score_str = f"{scores.overall_score:.1f}/10" if scores else "—"

    lines += [
        "## Overall Verdict",
        "",
        f"| Deal Quality | Deal Score | Page Score | Value Assessment |",
        f"|:---:|:---:|:---:|:---:|",
        f"| {quality_badge} | **{s.deal_quality_score:.1f}/10** | **{page_score_str}** | {value_badge} |",
        "",
    ]

    # Use structured overall_verdict if available (Improvement 2), else fallback to value_reasoning
    verdict_text = s.overall_verdict or s.value_reasoning
    if verdict_text:
        lines += [f"> {verdict_text}", ""]

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

    # Stale content warnings — surfaced directly from the scraper (Bug 3).
    # These are shown even if audit_ai hasn't run yet, ensuring the issue is
    # always visible in the research report regardless of AI flag propagation.
    stale_warnings = getattr(a, "stale_content_warnings", [])
    if stale_warnings:
        lines += [
            "### 🕰️ Stale Content Warnings",
            "",
            "_The following text was found on the **live deal page** and appears outdated. "
            "This is an active problem — not a historical note._",
            "",
        ]
        for w in stale_warnings:
            lines.append(
                f"- **[{w.get('location', '?')}]** {w.get('signal', 'Stale signal')}: "
                f'"{w.get("text", "")[:150]}"'
            )
        lines += ["", "---", ""]

    # ── Competitor Pricing ───────────────────────────────────────────────────
    lines += ["## Competitor Pricing", ""]
    groupon_price = _groupon_price(a)
    if groupon_price is not None:
        lines.append(f"**Groupon deal price:** ${groupon_price:,.2f}")
        lines.append("")

    if r.competitors:
        # Separate direct competitors from aggregators/tangential for display
        direct = [c for c in r.competitors if getattr(c, "match_type", "close") not in ("aggregator", "tangential")]
        related = [c for c in r.competitors if getattr(c, "match_type", "close") in ("aggregator", "tangential")]

        def _conf_badge(conf: float) -> str:
            if conf >= 0.7:
                return "🟢 High"
            elif conf >= 0.4:
                return "🟡 Medium"
            else:
                return "🔴 Low"

        if direct:
            lines += [
                "| Competitor | Service | Regular Price | Sale Price | Confidence | Source |",
                "|------------|---------|:---:|:---:|:---:|--------|",
            ]
            for c in direct:
                reg = f"${c.regular_price:,.2f}" if c.regular_price is not None else "—"
                sale = f"${c.sale_price:,.2f}" if c.sale_price is not None else "—"
                domain = _domain(c.source_url)
                conf = getattr(c, "sku_match_confidence", 0.5)
                conf_cell = _conf_badge(conf)
                lines.append(
                    f"| {c.competitor_name} | {c.service_name} | {reg} | {sale} "
                    f"| {conf_cell} | [{domain}]({c.source_url}) |"
                )
            lines.append("")

        # Compute average using only high-confidence (≥60%) entries (Bug 4 fix)
        high_conf_prices = [
            c.regular_price for c in direct
            if c.regular_price is not None
            and getattr(c, "sku_match_confidence", 0.5) >= 0.6
        ]
        high_conf_count = len(high_conf_prices)

        if high_conf_count >= 2:
            avg_price = sum(high_conf_prices) / high_conf_count
            lines.append(
                f"**Competitor average (high-confidence only, N={high_conf_count}):** "
                f"${avg_price:,.2f}"
            )
            if groupon_price is not None:
                pct_off = (avg_price - groupon_price) / avg_price * 100
                lines.append(
                    f"**Groupon vs. market:** ${avg_price - groupon_price:,.2f} savings "
                    f"({pct_off:.0f}% below average)"
                )
        elif high_conf_count == 1:
            lines += [
                "> ⚠️ **Insufficient high-confidence competitor data (N=1).** "
                "Pricing comparison should be treated as directional only.",
            ]
            avg_price = high_conf_prices[0]
            if groupon_price is not None:
                lines.append(
                    f"_Single high-confidence reference: ${avg_price:,.2f} — "
                    f"${avg_price - groupon_price:,.2f} implied savings (directional)._"
                )
        else:
            lines += [
                "> ⚠️ **No high-confidence competitor data found (N=0).** "
                "Pricing comparison is not reliable. Do not cite specific savings figures.",
            ]

        lines.append("")

        # Related pricing context (aggregators / tangential)
        if related:
            lines += ["#### Related Pricing Context (excluded from average)", ""]
            lines += [
                "| Source | Type | Price | Note |",
                "|--------|------|:---:|------|",
            ]
            for c in related:
                reg = f"${c.regular_price:,.2f}" if c.regular_price is not None else "—"
                match = getattr(c, "match_type", "?")
                note = c.notes or ""
                lines.append(f"| {c.competitor_name} | {match} | {reg} | {note[:60]} |")
            lines += ["", "_These sources are excluded from average calculations._", ""]
    else:
        lines.append("_No competitor pricing data found._")
        lines.append("")

    lines += ["---", ""]

    # ── Merchant Reputation ──────────────────────────────────────────────────
    lines += ["## Merchant Reputation", ""]

    merchant = a.merchant_name or "this merchant"
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
        else:
            # Yelp was searched but no verified listing was found for this merchant.
            lines.append(f"| Yelp | — | — | No Yelp listing verified for {merchant} |")
        if r.google:
            rating_str = f"{r.google.rating} ★" if r.google.rating else "—"
            count_str = str(r.google.review_count) if r.google.review_count else "—"
            src = r.google.source_url or ""
            src_link = f"[Google]({src})" if src else "Google"
            lines.append(f"| Google | {rating_str} | {count_str} | {src_link} |")
        else:
            lines.append(f"| Google | — | — | No Google listing verified for {merchant} |")
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
        lines.append(f"_No external reputation data found for {merchant}._")
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

    # ── Data Quality / Failure Report (Priority 10) ──────────────────────────
    quality = getattr(r, "quality", None)
    if quality:
        confidence_pct = f"{quality.overall_confidence:.0%}"
        conf_icon = "🟢" if quality.overall_confidence >= 0.7 else (
            "🟡" if quality.overall_confidence >= 0.4 else "🔴"
        )
        lines += [
            "## Research Data Quality",
            "",
            f"| Metric | Status |",
            f"|--------|--------|",
            f"| Overall confidence | {conf_icon} **{confidence_pct}** |",
            f"| Competitor pricing | `{quality.competitor_pricing_status}` |",
            f"| Merchant reputation | `{quality.merchant_reputation_status}` |",
            f"| Category context | `{quality.category_context_status}` |",
            f"| Deal page pricing verifiable | {'✅ Yes' if quality.pricing_verifiable else '❌ No — original prices not captured'} |",
            f"| Sources accepted / found | {quality.sources_accepted} / {quality.sources_found} |",
            "",
        ]
        if quality.overall_confidence < 0.4:
            lines += [
                "> ⚠️ **LOW CONFIDENCE**: Insufficient research data. "
                "Price comparisons and market assessments in this report are estimates only.",
                "",
            ]
        lines += ["---", ""]

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


def _resolve_location(a: DealAudit) -> Optional[str]:
    """
    Return location string for a deal audit, falling back to the audit JSON
    file if city/state are not populated on the in-memory object.

    Values are validated before use: if a "city" field contains page copy
    (e.g. "2 months per course starting from your enrollment date") rather
    than an actual place name, it is discarded and None is returned so the
    report shows "—" rather than garbage text.
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


def _validated_city(city: Optional[str]) -> Optional[str]:
    """
    Return city only if it looks like a real place name; otherwise None.
    Prevents page-copy fragments from leaking into the location field.
    """
    if not city:
        return None
    # Reject if the string is too long, contains digits mid-string (not a zip),
    # or contains sentence-structure words that indicate it is page copy.
    _NOT_CITY = re.compile(
        r"\b(month|week|day|year|course|enrollment|starting|from your|per|"
        r"purchase|voucher|valid|expires?|redeemable|redeem|subject|prior|"
        r"book|appointment|advance|required|included|exclud|limit|maximum|"
        r"minimum|must|cannot|please|contact|call|visit|online|http|www)\b",
        re.IGNORECASE,
    )
    if len(city) > 40 or _NOT_CITY.search(city):
        return None
    # Must look like a place: letters, spaces, hyphens, apostrophes, periods only
    if not re.match(r"^[A-Za-z][A-Za-z\s\-\'\.]{0,39}$", city):
        return None
    return city


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
