"""
Yelp scraper — finds and extracts a merchant's Yelp listing.

Strategy:
  1. Search for the Yelp URL via web_search (Google/SerpAPI)
  2. Fetch the Yelp page with httpx + realistic headers
  3. Try JSON-LD extraction first (most structured)
  4. Fall back to JSON embedded in <script> tags (Yelp's Next.js data)
  5. Fall back to HTML extraction with BeautifulSoup
  6. Cap at 20 review texts for AI theme analysis

Why not Playwright? Yelp's initial HTML render (server-side) contains enough
data for our purposes. If httpx returns a bot challenge, we log it and return
partial data rather than crashing the pipeline.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from models import YelpData, YelpReview
from researcher.web_search import search

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}


async def scrape_yelp(merchant_name: str, city: str) -> Optional[YelpData]:
    """
    Find the merchant on Yelp and extract rating, review count, and review texts.
    Returns None if the merchant cannot be found or scraping fails.
    """
    yelp_url = await _find_yelp_url(merchant_name, city)
    if not yelp_url:
        log.info("No Yelp URL found for %r in %s", merchant_name, city)
        return None

    return await _scrape_yelp_page(yelp_url, merchant_name)


async def _find_yelp_url(merchant_name: str, city: str) -> Optional[str]:
    """Search for the Yelp business listing URL."""
    query = f'site:yelp.com/biz "{merchant_name}" {city}'
    results = await search(query, num_results=5)

    for r in results:
        if "yelp.com/biz/" in r.url and "?_fsig" not in r.url:
            # Clean off query params that redirect to search
            url = r.url.split("?")[0]
            log.debug("Found Yelp URL: %s", url)
            return url

    # Fallback: general search
    query2 = f'"{merchant_name}" {city} yelp reviews'
    results2 = await search(query2, num_results=8)
    for r in results2:
        if "yelp.com/biz/" in r.url:
            return r.url.split("?")[0]

    return None


async def _scrape_yelp_page(url: str, merchant_name: str) -> Optional[YelpData]:
    """Fetch the Yelp page and extract all useful data."""
    async with httpx.AsyncClient(
        timeout=20.0,
        headers=_HEADERS,
        follow_redirects=True,
    ) as client:
        try:
            resp = await client.get(url)
            if resp.status_code == 403:
                log.warning("Yelp returned 403 (bot challenge) for %s", url)
                # Return partial data with just the URL so we still log the source
                return YelpData(url=url, name=merchant_name)
            resp.raise_for_status()
            html = resp.text
        except Exception as exc:
            log.warning("Yelp fetch failed for %s: %s", url, exc)
            return None

    soup = BeautifulSoup(html, "lxml")

    # Strategy 1: JSON-LD
    data = _extract_json_ld(soup)
    if data:
        return data

    # Strategy 2: Embedded Next.js / React data blob
    data = _extract_next_data(soup, url)
    if data:
        return data

    # Strategy 3: HTML extraction
    return _extract_html(soup, url, merchant_name)


def _extract_json_ld(soup: BeautifulSoup) -> Optional[YelpData]:
    """Try to extract from JSON-LD structured data."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            obj = json.loads(script.string or "")
            if isinstance(obj, list):
                obj = next((o for o in obj if o.get("@type") in ("LocalBusiness", "Restaurant", "HealthAndBeautyBusiness", "SportsActivityLocation")), None)
            if not isinstance(obj, dict):
                continue

            rating_obj = obj.get("aggregateRating", {})
            return YelpData(
                name=obj.get("name"),
                url=obj.get("url", ""),
                rating=_safe_float(rating_obj.get("ratingValue")),
                review_count=_safe_int(rating_obj.get("reviewCount")),
                categories=[
                    c if isinstance(c, str) else c.get("name", "")
                    for c in (obj.get("servesCuisine") or obj.get("category") or [])
                ],
                reviews=[],
                review_texts=[],
            )
        except (json.JSONDecodeError, AttributeError):
            continue
    return None


def _extract_next_data(soup: BeautifulSoup, url: str) -> Optional[YelpData]:
    """Try to extract from __NEXT_DATA__ or similar embedded JSON."""
    # Yelp embeds business data in a <script id="__NEXT_DATA__"> tag
    next_script = soup.find("script", id="__NEXT_DATA__")
    if not next_script:
        return None

    try:
        raw = json.loads(next_script.string or "")
        # Navigate to the business data — path varies by Yelp page version
        props = raw.get("props", {}).get("pageProps", {})
        biz = (
            props.get("businessState", {}).get("bizDetailsPageProps", {})
            or props.get("initialState", {}).get("bizDetailsPageProps", {})
            or {}
        )

        rating = (
            biz.get("rating")
            or biz.get("businessContactProps", {}).get("rating")
        )
        review_count = biz.get("reviewCount") or biz.get("numReviews")
        name = biz.get("name") or biz.get("businessName")

        # Extract review texts from the reviews list
        reviews_raw = biz.get("reviews", []) or []
        review_texts = []
        reviews = []
        for r in reviews_raw[:20]:
            text = r.get("comment", {}).get("text") or r.get("text", "")
            if text:
                review_texts.append(text)
                reviews.append(YelpReview(
                    author=r.get("user", {}).get("markupDisplayName"),
                    rating=_safe_float(r.get("rating")),
                    text=text,
                    date=r.get("localizedDate") or r.get("timeCreated", "")[:10],
                ))

        if name or rating:
            return YelpData(
                name=name,
                url=url,
                rating=_safe_float(rating),
                review_count=_safe_int(review_count),
                reviews=reviews,
                review_texts=review_texts,
            )
    except Exception as exc:
        log.debug("__NEXT_DATA__ extraction failed: %s", exc)

    return None


def _extract_html(
    soup: BeautifulSoup,
    url: str,
    merchant_name: str,
) -> Optional[YelpData]:
    """Last-resort HTML extraction."""
    name_el = soup.select_one("h1") or soup.find(attrs={"data-testid": "business-name"})
    name = name_el.get_text(strip=True) if name_el else merchant_name

    # Rating — look for aria-label like "4.5 star rating"
    rating: Optional[float] = None
    rating_el = soup.find(attrs={"aria-label": re.compile(r"(\d\.?\d?)\s+star", re.I)})
    if rating_el:
        m = re.search(r"(\d\.?\d?)\s+star", rating_el.get("aria-label", ""), re.I)
        if m:
            rating = float(m.group(1))

    # Review count
    review_count: Optional[int] = None
    rc_text = soup.get_text()
    m = re.search(r"([\d,]+)\s+reviews?", rc_text, re.I)
    if m:
        review_count = int(m.group(1).replace(",", ""))

    # Review texts from <p> tags inside review containers
    review_texts: list[str] = []
    reviews: list[YelpReview] = []
    for container in soup.select(
        "[data-testid='review-comment'], [class*='review-content'], .review"
    )[:20]:
        p = container.find("p")
        if p:
            text = p.get_text(strip=True)
            if len(text) > 30:  # skip stub reviews
                review_texts.append(text)
                reviews.append(YelpReview(text=text))

    return YelpData(
        name=name,
        url=url,
        rating=rating,
        review_count=review_count,
        reviews=reviews,
        review_texts=review_texts,
    )


def _safe_float(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _safe_int(v) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None
