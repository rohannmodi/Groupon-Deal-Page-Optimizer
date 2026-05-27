"""
Markdown reporter — renders a human-readable audit summary.

Uses inline template (Jinja2 available for complex templates later).
Produces outputs/{deal_id}/1_audit/audit_summary.md
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from models import DealAudit, PricingOption
from config.settings import settings


def write_audit_markdown(audit: DealAudit) -> Path:
    """Render audit_summary.md and return its path."""
    out_dir = settings.outputs_dir / audit.deal_id / "1_audit"
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / "audit_summary.md"
    out_path.write_text(_render(audit), encoding="utf-8")
    return out_path


def _render(a: DealAudit) -> str:
    lines: list[str] = []

    # ── Header ──────────────────────────────────────────────────────────────
    lines += [
        f"# Deal Audit: {a.title or 'Untitled Deal'}",
        "",
        f"**URL:** {a.url}  ",
        f"**Deal ID:** `{a.deal_id}`  ",
        f"**Scraped:** {a.scraped_at.strftime('%Y-%m-%d %H:%M UTC') if a.scraped_at else 'unknown'}  ",
        f"**Duration:** {a.scrape_duration_seconds or '?'}s  ",
        "",
    ]

    if a.scrape_error:
        lines += [f"> ⚠️ **Scrape warnings:** {a.scrape_error}", ""]

    # ── Identity ─────────────────────────────────────────────────────────────
    lines += ["## Deal Identity", ""]
    lines += _kv_table({
        "Merchant": a.merchant_name,
        "Category": a.category,
        "Location": _location(a.city, a.state),
        "Title": a.title,
        "Subtitle": a.subtitle,
    })
    lines.append("")

    # ── Pricing ──────────────────────────────────────────────────────────────
    lines += ["## Pricing Options", ""]
    if a.pricing_options:
        lines.append("| Option | Deal Price | Original | Discount | Savings |")
        lines.append("|--------|-----------|----------|----------|---------|")
        for opt in a.pricing_options:
            primary_badge = " ⭐" if opt.is_primary else ""
            lines.append(
                f"| {opt.option_name}{primary_badge} "
                f"| {_price(opt.deal_price)} "
                f"| {_price(opt.original_price)} "
                f"| {_pct(opt.discount_pct)} "
                f"| {_price(opt.savings_amount)} |"
            )
    else:
        lines.append("_No pricing data extracted._")
    lines.append("")

    # ── Highlights ───────────────────────────────────────────────────────────
    lines += ["## Highlights / What You Get", ""]
    if a.highlights:
        for h in a.highlights:
            lines.append(f"- {h}")
    else:
        lines.append("_None extracted._")
    lines.append("")

    # ── Fine Print ───────────────────────────────────────────────────────────
    lines += ["## Fine Print / Terms", ""]
    if a.fine_print:
        for fp in a.fine_print:
            lines.append(f"- {fp}")
    else:
        lines.append("_None extracted._")
    lines.append("")

    # ── Images ───────────────────────────────────────────────────────────────
    lines += ["## Images", ""]
    lines += _kv_table({
        "Total images": str(len(a.images)),
        "With alt text": str(sum(1 for i in a.images if i.alt_text)),
        "Types": _type_summary(a.images),
    })
    if a.images:
        lines += ["", "| # | Type | Width | Alt Text |", "|---|------|-------|----------|"]
        for i, img in enumerate(a.images[:10], 1):
            alt = (img.alt_text or "_none_")[:60]
            lines.append(f"| {i} | {img.image_type} | {img.estimated_width or '?'}px | {alt} |")
        if len(a.images) > 10:
            lines.append(f"| … | _+{len(a.images) - 10} more_ | | |")
    lines.append("")

    # ── Reviews ──────────────────────────────────────────────────────────────
    lines += ["## Reviews", ""]
    lines += _kv_table({
        "Average rating": f"{a.reviews_avg_rating} ★" if a.reviews_avg_rating else "—",
        "Review count": str(a.reviews_count) if a.reviews_count else "—",
    })
    if a.review_samples:
        lines.append("")
        for rs in a.review_samples[:3]:
            byline = f"*{rs.author or 'Anonymous'}*"
            if rs.rating:
                byline += f" — {rs.rating}★"
            lines += [f"> {rs.text[:300]}", f"> {byline}", ""]
    lines.append("")

    # ── Trust & Urgency ───────────────────────────────────────────────────────
    lines += ["## Trust Signals", ""]
    if a.trust_signals:
        for ts in a.trust_signals:
            lines.append(f"- **{ts.signal_type}:** {ts.signal_value}")
    else:
        lines.append("_None detected._")
    lines.append("")

    lines += ["## Urgency Elements", ""]
    if a.urgency_elements:
        for ue in a.urgency_elements:
            atf = " _(above fold)_" if ue.is_visible_atf else ""
            lines.append(f"- **{ue.element_type}:** {ue.element_text}{atf}")
    else:
        lines.append("_None detected._")
    lines.append("")

    # ── SEO ──────────────────────────────────────────────────────────────────
    lines += ["## SEO Elements", ""]
    seo = a.seo
    lines += _kv_table({
        "Meta title": seo.meta_title,
        "Meta description": seo.meta_description,
        "H1": seo.h1_text,
        "H2 count": str(len(seo.h2_texts)),
        "Schema markup": f"Yes ({seo.schema_type})" if seo.has_schema_markup else "No",
        "Canonical URL": seo.canonical_url,
        "OG title": seo.og_title,
        "Images on page": str(seo.image_count),
        "Script tags": str(seo.script_count),
    })
    if seo.h2_texts:
        lines += ["", "**H2 headings:**"]
        for h2 in seo.h2_texts[:5]:
            lines.append(f"- {h2}")
    lines.append("")

    # ── FAQs ─────────────────────────────────────────────────────────────────
    if a.faqs:
        lines += ["## FAQs", ""]
        for faq in a.faqs:
            lines += [f"**Q: {faq.question}**", f"A: {faq.answer}", ""]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _price(v: Optional[float]) -> str:
    return f"${v:,.2f}" if v is not None else "—"


def _pct(v: Optional[float]) -> str:
    return f"{v:.0f}%" if v is not None else "—"


def _location(city: Optional[str], state: Optional[str]) -> Optional[str]:
    parts = [p for p in (city, state) if p]
    return ", ".join(parts) if parts else None


def _kv_table(data: dict) -> list[str]:
    rows = []
    for k, v in data.items():
        if v:
            rows.append(f"- **{k}:** {v}")
    return rows


def _type_summary(images) -> str:
    from collections import Counter
    counts = Counter(i.image_type for i in images)
    return ", ".join(f"{t} ({n})" for t, n in counts.most_common())
