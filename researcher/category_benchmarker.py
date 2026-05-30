"""
Category benchmarker — establishes market context for a deal's category.

v2 improvements:
  - Domain validation: rejects informational sites (Investopedia, BLS, tourism boards)
  - Source tracking: reports sources_found / sources_accepted / sources_rejected
  - Category-aware queries using the same detection logic as competitor_finder
  - Only uses snippets from plausibly relevant pages

A SearchResult that passes domain + relevance validation is "accepted."
Rejected results are logged but still stored in the CategoryContext for
traceability — they just don't influence the price range extraction.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from models import CategoryContext, SearchResult
from researcher.web_search import search
from researcher.competitor_finder import (
    _REJECT_DOMAINS,
    _clean_service_type,
    _detect_category,
    _QUERY_TEMPLATES,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Category → service term mapping (for price context queries)
# ---------------------------------------------------------------------------

_CATEGORY_SERVICE_MAP: dict[str, str] = {
    "juice_cleanse": "juice cleanse",
    "facial": "facial treatment",
    "massage": "massage therapy",
    "spa": "spa day package",
    "fitness_class": "fitness yoga class",
    "hair_salon": "hair salon services",
    "nails": "manicure pedicure",
    "waxing": "waxing services",
    "escape_room": "escape room",
    "bowling": "bowling per game",
    "movie_ticket": "movie ticket",
    "restaurant": "restaurant dining",
    "automotive": "oil change service",
    "car_wash": "car detailing",
    "aesthetics": "laser aesthetics treatment",
    "dental": "teeth whitening",
    "photography": "photography session",
    "food_class": "cooking class",
    "tour": "guided city tour",
    "gym": "gym personal training",
    "online_course": "online certification course",
    "general": "service",
}

# Snippets/titles from informational / non-competitor pages — reject these.
# Both exact and prefix matches checked (see _is_relevant_result).
_INFO_SIGNALS = [
    # Generic cost/info guides
    "how much does", "how much do", "how much is", "how much are",
    "average cost of", "average costs", "average price of", "average prices",
    "what does", "what is the cost", "what is the average",
    "cost of living", "price guide", "pricing guide",
    "salary", "annual report", "statistics show",
    "according to", "study found", "national average", "median income",
    "bls.gov", "investopedia", "nerdwallet", "bankrate", "consumeraffairs",
    # Tourism / destination content
    "tourism", "travel guide", "visit ", "things to do", "best places",
    "top things", "travel tips",
    # Automotive — for non-auto categories
    "car maintenance", "car service cost", "car repair", "auto repair",
    "depreciation", "msrp", "car payment", "insurance rate",
    "toyota", "honda", "ford", "chevrolet", "vehicle",
    # Financial / investment content
    "stock", "investment", "401k", "ira ", "mutual fund",
    # News / editorial
    "according to experts", "report says", "data shows",
]


def _matches_info_signal(text: str) -> bool:
    """Check if text (title+snippet) matches any informational content signal."""
    lower = text.lower()
    return any(sig in lower for sig in _INFO_SIGNALS)


def _is_relevant_result(result: SearchResult, service_term: str) -> bool:
    """Return True if the search result looks like market pricing data, not generic info."""
    url_lower = result.url.lower()

    # Domain-level rejection
    from urllib.parse import urlparse
    domain = urlparse(result.url).netloc.lower().lstrip("www.")
    clean_domain = domain
    if clean_domain in _REJECT_DOMAINS:
        return False
    if clean_domain.endswith(".gov") or clean_domain.endswith(".edu"):
        return False

    # Snippet-level signal rejection — catches informational pages even if domain passes
    combined = result.snippet + " " + result.title
    if _matches_info_signal(combined):
        return False

    # Must contain at least one price mention to be useful
    has_price = bool(re.search(r"\$\s*\d+", result.snippet))
    if not has_price:
        return False

    return True


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def get_category_context(
    category: str,
    city: str,
    deal_price: Optional[float] = None,
    service_type: str = "",
) -> CategoryContext:
    """
    Research market pricing context for a category in a city.
    Returns a CategoryContext with source acceptance statistics.
    """
    cat_key = _detect_category(service_type or category, category)
    # For generic / online-course categories, search by the actual product
    # (e.g. "clinical medical technician ekg course") rather than the literal
    # word "service", which previously pulled in unrelated (automotive) pricing.
    if cat_key in ("general", "online_course"):
        service_term = (
            _clean_service_type(service_type)
            or category
            or _CATEGORY_SERVICE_MAP[cat_key]
        )
    else:
        service_term = _CATEGORY_SERVICE_MAP.get(cat_key, category or "service")

    # Online courses are location-independent — don't tie the query to a city.
    if cat_key == "online_course":
        location = "online"
    else:
        location = city.strip() if city.strip() else "United States"

    # Build queries specifically for market pricing context
    queries = [
        f"{service_term} price {location}",
        f"how much does {service_term} cost {location}",
        f"{service_term} average pricing {location}",
    ]

    all_results: list[SearchResult] = []
    for query in queries:
        results = await search(query, num_results=6)
        all_results.extend(results)

    # Validate and track
    accepted: list[SearchResult] = []
    rejected: list[SearchResult] = []
    for r in all_results:
        if _is_relevant_result(r, service_term):
            accepted.append(r)
        else:
            rejected.append(r)
            log.debug(
                "Category research: rejected %s — %s", r.url[:60], r.title[:50]
            )

    log.info(
        "Category context for %r in %s: %d found, %d accepted, %d rejected",
        service_term, location, len(all_results), len(accepted), len(rejected),
    )

    # Extract price range only from accepted results
    low, high = _extract_price_range_from_snippets(accepted)
    discount_pct = _infer_typical_discount(cat_key)
    # Sanity-check the extracted price range against known category bounds.
    # If it looks wildly off (e.g., $350-$725 for a juice cleanse), flag it
    # as potentially contaminated rather than silently passing garbage to the AI.
    contaminated = _is_range_contaminated(cat_key, low, high)
    if contaminated:
        log.warning(
            "Category context contamination detected for %r: "
            "price range $%s–$%s is outside expected bounds for category %r. "
            "Discarding range to prevent false market comparisons.",
            service_term, low, high, cat_key,
        )
        low, high = None, None

    notes = _build_notes(service_term, location, deal_price, low, high,
                         len(accepted), len(rejected), contaminated)

    return CategoryContext(
        category=category,
        city=city,
        typical_price_low=low,
        typical_price_high=high,
        typical_discount_pct=discount_pct,
        notes=notes,
        search_results=accepted[:8],
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_price_range_from_snippets(
    results: list[SearchResult],
) -> tuple[Optional[float], Optional[float]]:
    """Extract price range from validated search result snippets."""
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
    n = len(all_prices)
    low = all_prices[max(0, n // 4)]
    high = all_prices[min(n - 1, 3 * n // 4)]
    return round(low, 2), round(high, 2)


def _infer_typical_discount(cat_key: str) -> Optional[float]:
    """Typical Groupon discount % for the category based on known patterns."""
    discounts = {
        "spa": 50.0, "massage": 50.0, "facial": 45.0,
        "hair_salon": 40.0, "nails": 35.0, "waxing": 40.0,
        "fitness_class": 55.0, "gym": 60.0,
        "escape_room": 20.0, "bowling": 30.0, "movie_ticket": 35.0,
        "restaurant": 20.0, "food_class": 35.0, "tour": 25.0,
        "automotive": 30.0, "car_wash": 35.0,
        "aesthetics": 50.0, "dental": 55.0,
        "photography": 40.0, "juice_cleanse": 35.0,
        "online_course": 70.0,
    }
    return discounts.get(cat_key)


def _is_range_contaminated(cat_key: str, low: Optional[float], high: Optional[float]) -> bool:
    """
    Returns True if the extracted price range is implausible for this category.
    Prevents cross-category contamination (e.g., car maintenance costs $350-$725
    appearing in a juice cleanse analysis).
    """
    if low is None or high is None:
        return False

    # Expected plausible max price per category (single unit, not year/fleet)
    _MAX_PLAUSIBLE: dict[str, float] = {
        "juice_cleanse": 500.0,    # 7-day cleanse can hit $400-$500
        "facial": 400.0,
        "massage": 300.0,
        "spa": 600.0,
        "fitness_class": 200.0,
        "hair_salon": 500.0,
        "nails": 150.0,
        "waxing": 200.0,
        "escape_room": 150.0,
        "bowling": 200.0,
        "movie_ticket": 50.0,
        "restaurant": 300.0,
        "automotive": 500.0,       # oil change / brakes
        "car_wash": 400.0,
        "aesthetics": 1500.0,
        "dental": 800.0,
        "photography": 800.0,
        "food_class": 400.0,
        "tour": 200.0,
        "gym": 200.0,
        "online_course": 1000.0,   # certification courses span a wide range
    }

    max_price = _MAX_PLAUSIBLE.get(cat_key, 1000.0)

    # Contaminated if the LOW end is above the max plausible price
    if low > max_price:
        return True

    # Contaminated if the range is implausibly wide (> 10x spread)
    if high > 0 and low > 0 and high / low > 10:
        return True

    return False


def _build_notes(
    service_term: str,
    city: str,
    deal_price: Optional[float],
    low: Optional[float],
    high: Optional[float],
    accepted: int,
    rejected: int,
    contaminated: bool = False,
) -> str:
    parts = []

    # Source quality report
    total = accepted + rejected
    if total > 0:
        parts.append(
            f"Research quality: {accepted}/{total} sources accepted "
            f"({rejected} rejected as non-competitor/informational)."
        )

    if contaminated:
        parts.append(
            "⚠ CONTAMINATION DETECTED: The extracted price range was implausible for "
            f"this category and has been discarded. Do NOT use it for market comparison. "
            "Category context is unreliable for this deal."
        )
        return " ".join(parts)

    if accepted == 0:
        parts.append("No validated market pricing found — treat price estimates as uncertain.")
        return " ".join(parts)

    if low and high:
        parts.append(f"Validated market range for {service_term} in {city}: ${low:.0f}–${high:.0f}")

    if deal_price and low and high:
        mid = (low + high) / 2
        if deal_price < low:
            parts.append(
                f"Groupon price (${deal_price:.2f}) is BELOW the validated market low "
                f"(${low:.0f}) — strong deal."
            )
        elif deal_price > high:
            parts.append(
                f"Groupon price (${deal_price:.2f}) is ABOVE the validated market range "
                f"(${low:.0f}–${high:.0f}) — verify value proposition."
            )
        else:
            savings = mid - deal_price
            parts.append(
                f"Groupon price (${deal_price:.2f}) is within validated market range. "
                f"Saves ~${savings:.0f} vs market midpoint."
            )

    return " ".join(parts) or f"Market pricing data for {service_term} in {city}."
