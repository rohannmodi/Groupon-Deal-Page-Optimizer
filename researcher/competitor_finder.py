"""
Competitor price finder.

For a given service + city, finds 3–5 competitors and their pricing.

Strategy:
  1. Build targeted search queries that exclude Groupon/LivingSocial
  2. For each result URL, attempt to scrape a price from the page
  3. Return CompetitorPrice objects with source URLs for citation

Price extraction uses regex over the page text — fast, no Playwright needed
for most booking/pricing pages which are server-rendered.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from models import CompetitorPrice, SearchResult
from researcher.web_search import search

log = logging.getLogger(__name__)

# Domains that are marketplaces/aggregators — not direct competitors
_SKIP_DOMAINS = {
    "groupon.com", "livingsocial.com", "yelp.com", "google.com",
    "tripadvisor.com", "angi.com", "thumbtack.com", "amazon.com",
    "facebook.com", "instagram.com", "twitter.com", "linkedin.com",
    "youtube.com", "wikipedia.org",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

_PRICE_RE = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")


async def find_competitors(
    service_type: str,
    city: str,
    groupon_price: Optional[float] = None,
    max_competitors: int = 5,
) -> list[CompetitorPrice]:
    """
    Find competitor businesses and pricing for a service in a city.

    Returns up to max_competitors CompetitorPrice objects.
    """
    queries = _build_queries(service_type, city)
    search_results: list[SearchResult] = []

    for query in queries:
        results = await search(query, num_results=6)
        search_results.extend(results)
        await asyncio.sleep(1.0)  # rate limit between searches

    # Deduplicate by domain, skip aggregators
    candidate_urls = _deduplicate_candidates(search_results)

    # Scrape prices from each candidate
    competitors: list[CompetitorPrice] = []
    tasks = [
        _scrape_competitor(url, service_type, groupon_price)
        for url in candidate_urls[:max_competitors + 2]  # fetch a few extra in case some fail
    ]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    for r in raw_results:
        if isinstance(r, CompetitorPrice):
            competitors.append(r)
            if len(competitors) >= max_competitors:
                break

    log.info(
        "Found %d competitors for %r in %s",
        len(competitors), service_type, city
    )
    return competitors


def _build_queries(service_type: str, city: str) -> list[str]:
    """Build search queries that find direct competitors."""
    return [
        f'"{service_type}" {city} price booking -groupon -livingsocial',
        f'{service_type} salon spa {city} prices -groupon',
        f'best {service_type} {city} cost',
    ]


def _deduplicate_candidates(results: list[SearchResult]) -> list[str]:
    """Return unique URLs, one per domain, skipping aggregator domains."""
    seen_domains: set[str] = set()
    urls: list[str] = []

    for r in results:
        if not r.url.startswith("http"):
            continue
        domain = urlparse(r.url).netloc.lower().lstrip("www.")
        if domain in _SKIP_DOMAINS or domain in seen_domains:
            continue
        seen_domains.add(domain)
        urls.append(r.url)

    return urls


async def _scrape_competitor(
    url: str,
    service_type: str,
    groupon_price: Optional[float],
) -> Optional[CompetitorPrice]:
    """Fetch a competitor page and extract name + price."""
    async with httpx.AsyncClient(
        timeout=12.0,
        headers=_HEADERS,
        follow_redirects=True,
    ) as client:
        try:
            resp = await client.get(url)
            if resp.status_code >= 400:
                return None
            html = resp.text
        except Exception as exc:
            log.debug("Competitor fetch failed for %s: %s", url, exc)
            return None

    soup = BeautifulSoup(html, "lxml")
    page_text = soup.get_text(separator=" ", strip=True)

    # Extract competitor name from title or h1
    name = _extract_name(soup, url)
    if not name:
        return None

    # Extract prices from page text
    prices = _extract_prices(page_text, service_type)
    if not prices and groupon_price:
        # No price found — still record the competitor but note price unknown
        return CompetitorPrice(
            competitor_name=name,
            service_name=service_type,
            source_url=url,
            notes="Price not found on page",
        )

    regular_price = prices[0] if prices else None
    # If multiple prices found, the highest is likely the regular price
    # and a lower one might be a sale/package price
    sale_price = min(prices) if len(prices) > 1 and min(prices) != regular_price else None

    return CompetitorPrice(
        competitor_name=name,
        service_name=service_type,
        regular_price=regular_price,
        sale_price=sale_price,
        source_url=url,
    )


def _extract_name(soup: BeautifulSoup, url: str) -> Optional[str]:
    """Extract business name from page."""
    # Try in order: meta og:site_name, h1, title
    og_site = soup.find("meta", property="og:site_name")
    if og_site and og_site.get("content"):
        return og_site["content"].strip()[:60]

    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)[:60]

    title = soup.find("title")
    if title:
        # Strip common suffixes like " | Book Online" or " - Chicago"
        text = title.get_text(strip=True)
        text = re.split(r"\s*[|\-–]\s*", text)[0].strip()
        return text[:60]

    # Last resort: domain name
    domain = urlparse(url).netloc.lstrip("www.")
    return domain.split(".")[0].title()


def _extract_prices(page_text: str, service_type: str) -> list[float]:
    """
    Extract price mentions from page text that are plausibly related to
    the service type. Returns prices sorted descending (highest first).
    """
    # Look for prices near service keywords
    service_words = set(re.findall(r"\b\w+\b", service_type.lower()))

    # Find all prices in the page
    all_matches = _PRICE_RE.findall(page_text)
    all_prices = []
    for m in all_matches:
        try:
            price = float(m.replace(",", ""))
            # Filter: ignore very small (tips) or very large (packages) amounts
            if 5 <= price <= 500:
                all_prices.append(price)
        except ValueError:
            pass

    if not all_prices:
        return []

    # Sort descending, take top 3 unique values
    unique = sorted(set(all_prices), reverse=True)
    return unique[:3]
