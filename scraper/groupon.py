"""
Groupon-specific scraper.

Fetches a deal page with Playwright, saves the raw HTML, then runs all parsers
to assemble a complete DealAudit.

Parsing priority (highest to lowest reliability):
  1. JSON-LD ProductGroup schema  — most stable, richest data
  2. __NEXT_DATA__ JSON           — good for fields not in JSON-LD
  3. HTML element selectors       — fallback for page-specific elements
  4. Regex over page text         — last resort

All parser failures are caught and recorded in audit.scrape_error rather
than crashing the pipeline.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from datetime import datetime

from bs4 import BeautifulSoup

from config.settings import settings
from models import DealAudit, SEOElements
from scraper.browser import fetch_page_with_retry
from scraper.parsers.deal_parser import (
    parse_category,
    parse_city_state,
    parse_description,
    parse_faqs,
    parse_fine_print,
    parse_highlights,
    parse_merchant_name,
    parse_redemption_address,
    parse_subtitle,
    parse_title,
)
from scraper.parsers.image_parser import parse_images
from scraper.parsers.jsonld_parser import (
    _looks_like_css,
    extract_description_from_jsonld,
    extract_fine_print_from_jsonld,
    extract_highlights_from_jsonld,
    extract_merchant_from_jsonld,
    extract_pricing_from_jsonld,
    extract_rating_from_jsonld,
    extract_review_count_from_jsonld,
    extract_review_samples_from_jsonld,
)
from scraper.parsers.next_data_parser import (
    extract_next_data,
    extract_description_from_next_data,
    extract_fine_print_from_next_data,
    extract_highlights_from_next_data,
    extract_merchant_from_next_data,
    extract_rating_from_next_data,
)
from scraper.parsers.pricing_parser import parse_pricing_options
from scraper.parsers.seo_parser import parse_seo
from scraper.parsers.trust_parser import (
    parse_reviews,
    parse_trust_signals,
    parse_urgency_elements,
)


async def scrape_deal(deal_id: str, url: str) -> DealAudit:
    """
    Fetch and parse a single Groupon deal page.

    Returns a DealAudit regardless of partial failures — errors are
    captured in audit.scrape_error so downstream stages can decide
    whether to skip or continue.
    """
    t_start = time.monotonic()
    errors: list[str] = []

    # ------------------------------------------------------------------ Fetch
    html: Optional[str] = None
    final_url: str = url
    try:
        html, final_url = await fetch_page_with_retry(url)
    except Exception as exc:
        return DealAudit(
            deal_id=deal_id,
            url=url,
            scrape_error=f"Fetch failed: {exc}",
            scrape_duration_seconds=time.monotonic() - t_start,
        )

    # ---------------------------------------------------------------- Save HTML
    raw_html_path: Optional[str] = None
    try:
        settings.ensure_dirs()
        html_file = settings.raw_html_dir / f"{deal_id}.html"
        html_file.write_text(html, encoding="utf-8")
        raw_html_path = str(html_file)
    except Exception as exc:
        errors.append(f"HTML save failed: {exc}")

    # ------------------------------------------------------------------ Parse
    soup = BeautifulSoup(html, "lxml")

    # Extract secondary data sources once (reused across fields)
    next_data = extract_next_data(soup)

    # ── Title / subtitle (HTML-rendered, H1 is reliable) ──────────────────────
    title = _safe(parse_title, soup, errors=errors, label="title")
    subtitle = _safe(parse_subtitle, soup, errors=errors, label="subtitle")

    # ── Merchant (JSON-LD brand.name > __NEXT_DATA__ > HTML) ─────────────────
    merchant = (
        _safe(extract_merchant_from_jsonld, soup, errors=errors, label="merchant_jsonld")
        or _safe(parse_merchant_name, soup, errors=errors, label="merchant_html")
        or (extract_merchant_from_next_data(next_data) if next_data else None)
    )

    # ── Category / city (HTML breadcrumbs — still works) ─────────────────────
    category = _safe(parse_category, soup, errors=errors, label="category")
    city, state = _safe_tuple(parse_city_state, soup, final_url, errors=errors, label="location")

    # ── Description — use the LONGEST source, not just the first non-empty one ──
    # JSON-LD description is often just the intro paragraph. The full HTML body
    # (id="about") contains subsections like "Available Escape Rooms", "What To
    # Expect", "How It Works" that the audit AI needs to see. We collect all three
    # candidates and pick the richest (longest) non-empty string.
    _desc_jsonld = _safe(extract_description_from_jsonld, soup, errors=errors, label="desc_jsonld")
    _desc_html = _safe(parse_description, soup, errors=errors, label="desc_html")
    _desc_next = (extract_description_from_next_data(next_data) if next_data else None)
    description = max(
        (d for d in [_desc_jsonld, _desc_html, _desc_next] if d),
        key=len,
        default=None,
    )

    # ── Highlights (JSON-LD 'What We Offer' > __NEXT_DATA__ > HTML) ──────────
    highlights = _safe_list(extract_highlights_from_jsonld, soup, errors=errors, label="highlights_jsonld")
    if not highlights:
        highlights = _safe_list(parse_highlights, soup, errors=errors, label="highlights_html")
    if not highlights and next_data:
        highlights = extract_highlights_from_next_data(next_data)

    # ── Fine print ───────────────────────────────────────────────────────────
    # Prefer the page's dedicated terms section ("Need To Know Info" / fine-print
    # tab) parsed from HTML — that is the authoritative terms & conditions block.
    # The JSON-LD description-derived extractor is only a fallback: some merchants
    # put marketing/curriculum copy (not terms) in their description, which would
    # otherwise leak course content into the fine print.
    fine_print = _safe_list(parse_fine_print, soup, errors=errors, label="fp_html")
    if not fine_print:
        fine_print = _safe_list(extract_fine_print_from_jsonld, soup, errors=errors, label="fp_jsonld")
    if not fine_print and next_data:
        fine_print = extract_fine_print_from_next_data(next_data)

    # Drop any CSS fragments that slipped through (merchant descriptions can embed
    # raw <style> blocks). Applies regardless of which source produced the list.
    highlights = [h for h in (highlights or []) if not _looks_like_css(h)]
    fine_print = [f for f in (fine_print or []) if not _looks_like_css(f)]

    # ── FAQs (JSON-LD FAQPage schema — deal_parser handles this) ─────────────
    faqs = _safe_list(parse_faqs, soup, errors=errors, label="faqs")

    # ── Pricing (JSON-LD hasVariant > pricing_parser HTML fallback) ──────────
    pricing = _safe_list(extract_pricing_from_jsonld, soup, errors=errors, label="pricing_jsonld")
    if not pricing:
        pricing = _safe_list(parse_pricing_options, soup, errors=errors, label="pricing_html")

    # ── Images / SEO / Trust / Urgency ───────────────────────────────────────
    images = _safe_list(parse_images, soup, errors=errors, label="images")
    seo = _safe(parse_seo, soup, errors=errors, label="seo")
    trust = _safe_list(parse_trust_signals, soup, errors=errors, label="trust")
    urgency = _safe_list(parse_urgency_elements, soup, errors=errors, label="urgency")

    # ── Reviews (JSON-LD reviews[] > HTML scraping) ───────────────────────────
    review_count_html, avg_rating_html, review_samples_html = _safe_reviews(soup, errors)

    # Prefer JSON-LD for rating and review count (most accurate)
    avg_rating = (
        _safe(extract_rating_from_jsonld, soup, errors=errors, label="rating_jsonld")
        or avg_rating_html
        or (extract_rating_from_next_data(next_data) if next_data else None)
    )
    review_count = (
        _safe(extract_review_count_from_jsonld, soup, errors=errors, label="review_count_jsonld")
        or review_count_html
    )
    review_samples_jsonld = _safe_list(
        extract_review_samples_from_jsonld, soup, errors=errors, label="reviews_jsonld"
    )
    review_samples = review_samples_jsonld or review_samples_html

    # ── Content freshness check ───────────────────────────────────────────────
    stale_warnings = _check_stale_content(
        highlights=highlights or [],
        fine_print=fine_print or [],
        description=description or "",
    )

    return DealAudit(
        deal_id=deal_id,
        url=final_url,
        title=title,
        subtitle=subtitle,
        merchant_name=merchant,
        category=category,
        city=city,
        state=state,
        redemption_address=_safe(parse_redemption_address, soup, errors=errors, label="address"),
        description=description,
        pricing_options=pricing or [],
        highlights=highlights or [],
        fine_print=fine_print or [],
        faqs=faqs or [],
        images=images or [],
        reviews_count=review_count,
        reviews_avg_rating=avg_rating,
        review_samples=review_samples or [],
        seo=seo or SEOElements(),
        trust_signals=trust or [],
        urgency_elements=urgency or [],
        stale_content_warnings=stale_warnings,
        scraped_at=datetime.utcnow(),
        scrape_duration_seconds=round(time.monotonic() - t_start, 2),
        raw_html_path=raw_html_path,
        scrape_error="; ".join(errors) if errors else None,
    )


# ---------------------------------------------------------------------------
# Content freshness check
# ---------------------------------------------------------------------------

_STALE_PATTERNS: list[tuple[str, str]] = [
    # (pattern_substring_lower, human_label)
    ("during this crisis", "COVID/crisis language"),
    ("during the crisis", "COVID/crisis language"),
    ("covid-19", "COVID-19 reference"),
    ("covid19", "COVID-19 reference"),
    ("coronavirus", "coronavirus reference"),
    ("pandemic", "pandemic reference"),
    ("social distancing", "social distancing reference"),
    ("due to covid", "COVID reference"),
    ("in light of covid", "COVID reference"),
    ("their top priority during", "crisis-era priority notice"),
    ("safety is our top priority", "crisis-era safety notice"),
    ("coming weeks", "vague future-date reference"),
    ("coming months", "vague future-date reference"),
    ("in the coming weeks", "vague future-date reference"),
    ("when we reopen", "closure/reopening language"),
    ("when we open back", "closure/reopening language"),
    ("temporarily closed", "closure language"),
    ("due to the current situation", "crisis-era language"),
]


def _check_stale_content(
    highlights: list[str],
    fine_print: list[str],
    description: str,
) -> list[dict]:
    """
    Scan extracted page content for staleness signals (COVID-era language,
    outdated notices, vague future-date references).

    Returns a list of dicts: {"text": <excerpt>, "location": <source section>}
    """
    warnings: list[dict] = []
    seen_texts: set[str] = set()

    def _scan(texts: list[str], location: str) -> None:
        for text in texts:
            text_lower = text.lower()
            for pattern, label in _STALE_PATTERNS:
                if pattern in text_lower:
                    key = f"{location}:{text[:80]}"
                    if key not in seen_texts:
                        seen_texts.add(key)
                        warnings.append({
                            "text": text[:200],
                            "location": location,
                            "signal": label,
                        })
                    break  # one warning per text item

    _scan(highlights, "highlights")
    _scan(fine_print, "fine_print")
    # Split description into sentences for finer-grained matching
    if description:
        import re as _re
        sentences = _re.split(r"(?<=[.!?])\s+", description[:3000])
        _scan(sentences, "description")

    return warnings


# ---------------------------------------------------------------------------
# Safe wrappers — catch parser exceptions individually
# ---------------------------------------------------------------------------

def _safe(fn, *args, errors: list[str], label: str):
    try:
        return fn(*args)
    except Exception as exc:
        errors.append(f"{label}: {exc}")
        return None


def _safe_list(fn, *args, errors: list[str], label: str) -> list:
    try:
        return fn(*args) or []
    except Exception as exc:
        errors.append(f"{label}: {exc}")
        return []


def _safe_tuple(fn, *args, errors: list[str], label: str) -> tuple:
    try:
        return fn(*args)
    except Exception as exc:
        errors.append(f"{label}: {exc}")
        return None, None


def _safe_reviews(soup: BeautifulSoup, errors: list[str]):
    try:
        return parse_reviews(soup)
    except Exception as exc:
        errors.append(f"reviews: {exc}")
        return None, None, []
