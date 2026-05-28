"""
Web search wrapper — SerpAPI primary, httpx+Google SERP fallback.

All searches return a list of SearchResult objects. The caller decides
what to do with them — we just fetch and parse.

Rate limiting: callers should await asyncio.sleep between searches.
We enforce nothing here — the runner.py manages the cadence.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional
from urllib.parse import quote_plus

import httpx

from config.settings import settings
from models import SearchResult

log = logging.getLogger(__name__)

_SERPAPI_URL = "https://serpapi.com/search.json"

# Headers for direct Google scraping (fallback)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


async def search(
    query: str,
    *,
    num_results: int = 8,
    timeout: float = 15.0,
) -> list[SearchResult]:
    """
    Run a web search and return up to num_results SearchResult objects.

    Uses SerpAPI if SERPAPI_KEY is configured, otherwise falls back to
    scraping Google search results with httpx.
    """
    if settings.serpapi_key:
        return await _serpapi_search(query, num_results=num_results, timeout=timeout)
    else:
        log.warning("SERPAPI_KEY not set — falling back to direct Google scrape")
        return await _google_scrape(query, num_results=num_results, timeout=timeout)


async def _serpapi_search(
    query: str,
    *,
    num_results: int,
    timeout: float,
) -> list[SearchResult]:
    params = {
        "q": query,
        "api_key": settings.serpapi_key,
        "num": num_results,
        "hl": "en",
        "gl": "us",
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.get(_SERPAPI_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.warning("SerpAPI request failed for %r: %s", query, exc)
            return []

    results: list[SearchResult] = []
    for i, item in enumerate(data.get("organic_results", [])[:num_results]):
        results.append(SearchResult(
            title=item.get("title", ""),
            url=item.get("link", ""),
            snippet=item.get("snippet", ""),
            position=i + 1,
        ))
    return results


async def _google_scrape(
    query: str,
    *,
    num_results: int,
    timeout: float,
) -> list[SearchResult]:
    """Scrape Google search results page — used when SerpAPI key is absent."""
    from bs4 import BeautifulSoup

    url = f"https://www.google.com/search?q={quote_plus(query)}&num={num_results}&hl=en"
    async with httpx.AsyncClient(timeout=timeout, headers=_HEADERS, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
        except Exception as exc:
            log.warning("Google scrape failed for %r: %s", query, exc)
            return []

    soup = BeautifulSoup(html, "lxml")
    results: list[SearchResult] = []

    for i, div in enumerate(soup.select("div.g, div[data-sokoban-container]")[:num_results]):
        a = div.select_one("a[href]")
        title_el = div.select_one("h3")
        snippet_el = div.select_one("[data-sncf], .VwiC3b, span.st")

        url_href = a["href"] if a else ""
        if url_href.startswith("/url?q="):
            url_href = url_href.split("?q=")[1].split("&")[0]

        if not url_href.startswith("http"):
            continue

        results.append(SearchResult(
            title=title_el.get_text(strip=True) if title_el else "",
            url=url_href,
            snippet=snippet_el.get_text(strip=True) if snippet_el else "",
            position=i + 1,
        ))

    return results
