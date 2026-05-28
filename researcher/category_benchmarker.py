"""
Category benchmarker — establishes market context for a deal's category.

Issues targeted search queries to determine:
  - Typical price range for the service in the city
  - Typical discount % for this category on Groupon
  - Any market-specific context (seasonal, demand patterns)

The raw search results are passed to the AI research synthesizer which
interprets them — we don't try to parse prices from snippets here.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from models import CategoryContext, SearchResult
from researcher.web_search import search

log = logging.getLogger(__name__)

# Map of category keywords → more searchable service terms
_CATEGORY_MAP: dict[str, str] = {
    "spa": "spa massage",
    "wellness": "wellness spa",
    "health": "health wellness",
    "beauty": "hair salon beauty",
    "hair": "hair salon haircut",
    "nail": "nail salon manicure",
    "massage": "massage therapy",
    "dental": "dental cleaning",
    "chiropractic": "chiropractic adjustment",
    "fitness": "gym membership",
    "yoga": "yoga class",
    "activities": "activity tour",
    "tours": "guided tour",
    "automotive": "oil change auto service",
    "restaurant": "restaurant dining",
    "food": "restaurant dining",
}


async def get_category_context(
    category: str,
    city: str,
    deal_price: Optional[float] = None,
) -> CategoryContext:
    """
    Research market pricing context for a category in a city.
    Returns a CategoryContext with search results for the AI to interpret.
    """
    service_term = _resolve_service_term(category)

    queries = [
        f"{service_term} average price {city}",
        f"how much does {service_term} cost {city} 2024",
        f"Groupon {service_term} typical discount percentage",
    ]

    all_results: list[SearchResult] = []
    for query in queries:
        results = await search(query, num_results=5)
        all_results.extend(results)

    # Quick heuristic: try to extract price range from snippets
    low, high = _extract_price_range_from_snippets(all_results)
    discount_pct = _infer_typical_discount(category)

    notes = _build_notes(service_term, city, deal_price, low, high)

    return CategoryContext(
        category=category,
        city=city,
        typical_price_low=low,
        typical_price_high=high,
        typical_discount_pct=discount_pct,
        notes=notes,
        search_results=all_results[:10],  # cap for context window
    )


def _resolve_service_term(category: str) -> str:
    """Map a deal category to a searchable service term."""
    category_lower = category.lower()
    for keyword, term in _CATEGORY_MAP.items():
        if keyword in category_lower:
            return term
    return category  # use as-is if no mapping found


def _extract_price_range_from_snippets(
    results: list[SearchResult],
) -> tuple[Optional[float], Optional[float]]:
    """
    Extract price range from search result snippets using regex.
    Looks for patterns like "$25–$50", "$30 to $60", "between $40 and $80".
    """
    price_re = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")
    all_prices: list[float] = []

    for r in results:
        for m in price_re.findall(r.snippet + " " + r.title):
            try:
                price = float(m.replace(",", ""))
                if 5 <= price <= 1000:
                    all_prices.append(price)
            except ValueError:
                pass

    if len(all_prices) < 2:
        return None, None

    all_prices.sort()
    # Use 25th and 75th percentile to get a representative range
    n = len(all_prices)
    low = all_prices[max(0, n // 4)]
    high = all_prices[min(n - 1, 3 * n // 4)]
    return round(low, 2), round(high, 2)


def _infer_typical_discount(category: str) -> Optional[float]:
    """
    Return a rough typical Groupon discount % for the category,
    based on known industry patterns. Used as context for the AI
    when search results don't yield a clear answer.
    """
    category_lower = category.lower()
    discounts = {
        "spa": 50.0,
        "massage": 50.0,
        "salon": 40.0,
        "hair": 40.0,
        "nail": 35.0,
        "beauty": 40.0,
        "dental": 55.0,
        "fitness": 60.0,
        "yoga": 55.0,
        "activities": 30.0,
        "tour": 25.0,
        "automotive": 30.0,
        "restaurant": 20.0,
    }
    for keyword, pct in discounts.items():
        if keyword in category_lower:
            return pct
    return None


def _build_notes(
    service_term: str,
    city: str,
    deal_price: Optional[float],
    low: Optional[float],
    high: Optional[float],
) -> str:
    """Compose a human-readable notes string for the context."""
    parts = []
    if low and high:
        parts.append(f"Typical market price for {service_term} in {city}: ${low:.0f}–${high:.0f}")
    if deal_price and low and high:
        mid = (low + high) / 2
        if deal_price < low:
            parts.append(f"Groupon price (${deal_price:.2f}) is below typical market low.")
        elif deal_price > high:
            parts.append(f"Groupon price (${deal_price:.2f}) is above typical market range — verify value.")
        else:
            savings = mid - deal_price
            parts.append(
                f"Groupon price (${deal_price:.2f}) is within market range. "
                f"Saves ~${savings:.0f} vs midpoint."
            )
    return " ".join(parts) or f"Market data for {service_term} in {city}."
