"""
Groupon-specific scraper.

Fetches a deal page with Playwright, saves the raw HTML, then runs all five
parsers to assemble a complete DealAudit. All parser failures are caught and
recorded in audit.scrape_error rather than crashing the pipeline.
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
    parse_subtitle,
    parse_title,
)
from scraper.parsers.image_parser import parse_images
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

    title = _safe(parse_title, soup, errors=errors, label="title")
    subtitle = _safe(parse_subtitle, soup, errors=errors, label="subtitle")
    merchant = _safe(parse_merchant_name, soup, errors=errors, label="merchant")
    category = _safe(parse_category, soup, errors=errors, label="category")
    city, state = _safe_tuple(parse_city_state, soup, final_url, errors=errors, label="location")
    description = _safe(parse_description, soup, errors=errors, label="description")
    highlights = _safe_list(parse_highlights, soup, errors=errors, label="highlights")
    fine_print = _safe_list(parse_fine_print, soup, errors=errors, label="fine_print")
    faqs = _safe_list(parse_faqs, soup, errors=errors, label="faqs")
    pricing = _safe_list(parse_pricing_options, soup, errors=errors, label="pricing")
    images = _safe_list(parse_images, soup, errors=errors, label="images")
    seo = _safe(parse_seo, soup, errors=errors, label="seo")
    trust = _safe_list(parse_trust_signals, soup, errors=errors, label="trust")
    urgency = _safe_list(parse_urgency_elements, soup, errors=errors, label="urgency")

    review_count, avg_rating, review_samples = _safe_reviews(soup, errors)

    return DealAudit(
        deal_id=deal_id,
        url=final_url,
        title=title,
        subtitle=subtitle,
        merchant_name=merchant,
        category=category,
        city=city,
        state=state,
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
        scraped_at=datetime.utcnow(),
        scrape_duration_seconds=round(time.monotonic() - t_start, 2),
        raw_html_path=raw_html_path,
        scrape_error="; ".join(errors) if errors else None,
    )


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
