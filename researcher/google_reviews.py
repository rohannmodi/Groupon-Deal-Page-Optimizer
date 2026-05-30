"""
Google Business data extractor.

Fetches the Google SERP for "{merchant_name} {city}" and extracts the
knowledge panel — rating, review count, address. This is the fastest way
to get Google business data without the Maps API or Playwright.

The knowledge panel is included in the initial HTML response from Google
for most established businesses.
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from models import GoogleData

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


async def scrape_google_business(
    merchant_name: str,
    city: str,
) -> Optional[GoogleData]:
    """
    Search Google for the business and extract rating/review count from
    the knowledge panel in the SERP.
    """
    query = f"{merchant_name} {city}"
    url = f"https://www.google.com/search?q={quote_plus(query)}&hl=en&gl=us"

    async with httpx.AsyncClient(
        timeout=15.0,
        headers=_HEADERS,
        follow_redirects=True,
    ) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
        except Exception as exc:
            log.warning("Google SERP fetch failed for %r: %s", query, exc)
            return None

    soup = BeautifulSoup(html, "lxml")
    return _extract_knowledge_panel(soup, merchant_name, url)


def _extract_knowledge_panel(
    soup: BeautifulSoup,
    merchant_name: str,
    source_url: str,
) -> Optional[GoogleData]:
    """
    Extract business rating and address from the knowledge panel.
    Google doesn't have a stable CSS class structure, so we use multiple
    strategies and take the first match.
    """
    rating: Optional[float] = None
    review_count: Optional[int] = None
    address: Optional[str] = None

    # ── Strategy 1: aria-label on star rating ────────────────────────────────
    for el in soup.find_all(attrs={"aria-label": re.compile(r"\d\.?\d?\s+(?:out of|star|stars)", re.I)}):
        label = el.get("aria-label", "")
        m = re.search(r"(\d\.?\d?)\s+(?:out of|star)", label, re.I)
        if m:
            rating = float(m.group(1))
            break

    # ── Strategy 2: look for "X.X (N reviews)" pattern in page text ──────────
    page_text = soup.get_text(separator=" ", strip=True)

    if rating is None:
        m = re.search(r"\b(\d\.\d)\s*\([\d,]+\s+(?:Google\s+)?reviews?\)", page_text)
        if m:
            rating = float(m.group(1))

    # ── Strategy 3: review count ─────────────────────────────────────────────
    if review_count is None:
        # Pattern: "(1,234 reviews)"  or  "1,234 Google reviews"
        for pattern in [
            r"\(([\d,]+)\s+(?:Google\s+)?reviews?\)",
            r"([\d,]+)\s+Google\s+reviews?",
        ]:
            m = re.search(pattern, page_text, re.I)
            if m:
                review_count = int(m.group(1).replace(",", ""))
                break

    # ── Strategy 4: address from knowledge panel ──────────────────────────────
    # Google wraps the address in a span with a data-attrid attribute
    addr_el = soup.find(attrs={"data-attrid": re.compile(r"address", re.I)})
    if addr_el:
        address = addr_el.get_text(strip=True)
    else:
        # Try the "X, City, State" pattern near the business name
        m = re.search(r"(\d+\s+\w+.*?,\s*[A-Z]{2}\s+\d{5})", page_text)
        if m:
            address = m.group(1)

    if rating is None and review_count is None:
        log.info("No Google knowledge panel found for %r", merchant_name)
        return None

    # Verify the page actually references this merchant before attributing a
    # rating to it. Without this, a redirect/blocked SERP or an unrelated
    # knowledge panel could be misreported as the merchant's reputation. (Problem 4)
    if not _page_mentions_merchant(merchant_name, page_text):
        log.warning(
            "Google SERP does not clearly reference %r — discarding rating to "
            "avoid attributing the wrong business's reputation.", merchant_name,
        )
        return None

    return GoogleData(
        name=merchant_name,
        rating=rating,
        review_count=review_count,
        address=address,
        source_url=source_url,
    )


def _page_mentions_merchant(merchant_name: str, page_text: str) -> bool:
    """
    Return True if the merchant's distinctive name tokens appear in the page.

    Generic words (spa, salon, the, ...) are ignored so a match requires the
    merchant's actual brand tokens — not just category words shared by any
    business in the results.
    """
    _NOISE = {
        "the", "a", "an", "and", "or", "of", "in", "at", "on", "for", "by", "to",
        "inc", "llc", "ltd", "co", "corp", "spa", "salon", "studio", "center",
        "centre", "clinic", "shop", "store", "services", "service", "courses",
        "course", "online",
    }
    tokens = {
        w for w in re.sub(r"[^a-z0-9\s]", " ", merchant_name.lower()).split()
        if len(w) >= 4 and w not in _NOISE
    }
    if not tokens:
        return True  # nothing distinctive to verify against — don't block
    text_lower = page_text.lower()
    hits = sum(1 for t in tokens if t in text_lower)
    # Require at least half of the distinctive tokens to appear.
    return hits >= max(1, len(tokens) // 2)
