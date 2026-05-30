"""
Competitor price finder — category-aware, confidence-scored.

Improvements over v1:
  - Category-specific search query templates (spa, automotive, escape rooms, etc.)
  - Domain validation rejects non-competitor sources (Investopedia, BLS, tourism sites)
  - SKU match confidence scoring: only claim price equality when confident
  - Merchant detection: flag direct merchant pages vs. aggregators/info sites

Quality bar for price comparisons:
  - sku_match_confidence >= 0.7 → "Competitor X charges $Y for the same service"
  - 0.4–0.7 → "Possible alternative: $Y (match not verified)"
  - < 0.4 → record but do not use for price comparison claims
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


# ---------------------------------------------------------------------------
# Domain validation
# ---------------------------------------------------------------------------

# Domains that are clearly NOT competitors — aggregators, news, info sites
_REJECT_DOMAINS = {
    # Marketplaces / deal sites
    "groupon.com", "livingsocial.com", "amazon.com", "ebay.com",
    # General info / encyclopedias
    "wikipedia.org", "wikihow.com", "investopedia.com", "about.com",
    "howstuffworks.com", "ehow.com", "reference.com",
    # News / media
    "cnn.com", "foxnews.com", "nbcnews.com", "cbsnews.com", "bbc.com",
    "huffpost.com", "buzzfeed.com", "vox.com", "theguardian.com",
    # Government / statistics
    "bls.gov", "census.gov", "irs.gov", "cdc.gov", "hhs.gov",
    "data.gov", "federalreserve.gov",
    # Finance / investment
    "bankrate.com", "nerdwallet.com", "creditkarma.com", "fool.com",
    "kiplinger.com", "moneyunder30.com",
    # Tourism boards (not direct competitors)
    "visitcalifornia.com", "visitlasvegas.com", "choosechicago.com",
    "nycgo.com", "discoverlosaangeles.com", "visitsantaclarita.com",
    "visitmilwaukee.org", "visitmadison.com",
    # Job sites
    "indeed.com", "glassdoor.com", "linkedin.com",
    # Social
    "facebook.com", "instagram.com", "twitter.com", "tiktok.com",
    "youtube.com", "reddit.com", "pinterest.com",
    # Review aggregators (only for review data, not price)
    "yelp.com", "tripadvisor.com", "google.com", "trustpilot.com",
    # Tech / automotive info (not competitors for services)
    "edmunds.com", "kbb.com", "carfax.com", "autotrader.com",
    "consumerreports.org", "cars.com",
    # Misc
    "quora.com", "stackoverflow.com", "medium.com", "substack.com",
}

# Domains that ARE valid competitor sources
_APPROVED_AGGREGATORS = {
    "yelp.com", "tripadvisor.com", "thumbtack.com", "angi.com",
    "homeadvisor.com", "vagaro.com", "mindbody.io", "booksy.com",
    "squareup.com", "fresha.com", "styleseat.com", "boulevard.io",
    "opentable.com", "resy.com", "tock.com",
    # Ticket/entertainment
    "fandango.com", "ticketmaster.com", "stubhub.com",
    # Car care
    "jiffy-lube.com", "midas.com", "firestone.com", "mavisdicount.com",
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


# ---------------------------------------------------------------------------
# Category detection
# ---------------------------------------------------------------------------

# Map service keywords → category key used for query template lookup
_CATEGORY_PATTERNS: list[tuple[list[str], str]] = [
    (["juice cleanse", "cleanse", "detox", "raw juice", "cold press"], "juice_cleanse"),
    (["facial", "hydrafacial", "microdermabrasion", "chemical peel", "skincare"], "facial"),
    (["massage", "swedish massage", "deep tissue", "hot stone", "sports massage"], "massage"),
    (["spa", "day spa", "wellness", "sauna", "float tank", "salt cave"], "spa"),
    (["yoga", "pilates", "barre", "spin class", "cycling class", "fitness class"], "fitness_class"),
    (["haircut", "hair salon", "blowout", "balayage", "highlights", "color"], "hair_salon"),
    (["nail", "manicure", "pedicure", "gel nails", "acrylic"], "nails"),
    (["wax", "waxing", "brazilian wax", "bikini wax", "laser hair"], "waxing"),
    (["escape room", "escape game"], "escape_room"),
    (["bowling", "bowling alley"], "bowling"),
    (["movie", "cinema", "theatre ticket", "film"], "movie_ticket"),
    (["restaurant", "dining", "food tour", "food tasting", "culinary"], "restaurant"),
    (["oil change", "lube", "car service", "brake", "tire rotation", "auto maintenance"], "automotive"),
    (["car wash", "detailing", "auto detailing", "car detailing"], "car_wash"),
    (["laser", "laser treatment", "tattoo removal", "coolsculpting", "botox", "filler"], "aesthetics"),
    (["teeth whitening", "dental", "teeth cleaning"], "dental"),
    (["photography", "photo shoot", "headshot", "portrait"], "photography"),
    (["cooking class", "wine tasting", "mixology", "cocktail class"], "food_class"),
    (["boat tour", "architecture tour", "city tour", "walking tour", "bus tour"], "tour"),
    (["gym", "personal training", "crossfit"], "gym"),
    # Online education / certification courses. Keep keywords course-specific so
    # they don't shadow categories like gym ("personal training"); this pattern
    # is also intentionally last so concrete service categories win first.
    ([
        "online course", "online class", "certification course", "certification",
        "certificate", "training course", "diploma", "bootcamp", "e-learning",
        "ekg", "ecg", "phlebotomy", "cpr certification", "acls", "bls",
        "medical technician", "real estate license", "tefl", "coding course",
        "online courses", "courses",
    ], "online_course"),
]


def _detect_category(service_type: str, category: str = "") -> str:
    """Return a category key for query template selection."""
    text = (service_type + " " + category).lower()
    for keywords, cat_key in _CATEGORY_PATTERNS:
        if any(kw in text for kw in keywords):
            return cat_key
    # Fallback: use first meaningful word from service type
    return "general"


# ---------------------------------------------------------------------------
# Category-specific search query templates
# ---------------------------------------------------------------------------

_QUERY_TEMPLATES: dict[str, list[str]] = {
    "juice_cleanse": [
        "{n}day juice cleanse price {city}",
        "juice cleanse delivery {city} buy",
        "cold press juice cleanse {city} cost",
    ],
    "facial": [
        "facial {city} price",
        "hydrafacial {city} cost booking",
        "spa facial {city} -groupon",
    ],
    "massage": [
        "massage {city} price per hour",
        "massage spa {city} book online -groupon",
        "therapeutic massage {city} cost",
    ],
    "spa": [
        "day spa {city} pricing",
        "spa package {city} -groupon -livingsocial",
        "spa day {city} cost book",
    ],
    "fitness_class": [
        "yoga class {city} drop-in price",
        "pilates class {city} price",
        "fitness class {city} monthly rates",
    ],
    "hair_salon": [
        "hair salon {city} haircut price",
        "hair color highlights {city} cost",
        "salon {city} pricing -groupon",
    ],
    "nails": [
        "nail salon {city} manicure pedicure price",
        "gel nails {city} cost",
        "nail spa {city} -groupon",
    ],
    "waxing": [
        "wax salon {city} price",
        "Brazilian wax {city} cost",
        "waxing services {city} -groupon",
    ],
    "escape_room": [
        "escape room {city} price per person",
        "escape game {city} book online cost",
        "escape room {city} tickets -groupon",
    ],
    "bowling": [
        "bowling alley {city} price per game",
        "bowling {city} lane rental cost",
        "bowling {city} -groupon",
    ],
    "movie_ticket": [
        "movie ticket {city} price",
        "AMC Regal Cinemark ticket price",
        "fandango movie tickets",
    ],
    "restaurant": [
        "restaurant {city} prix fixe menu price",
        "food tour {city} cost",
        "dining experience {city} price",
    ],
    "automotive": [
        "oil change {city} price",
        "car oil change {city} cost -groupon",
        "auto service {city} oil change pricing",
    ],
    "car_wash": [
        "car detailing {city} price",
        "car wash {city} full detail cost",
        "auto detailing {city} -groupon",
    ],
    "aesthetics": [
        "laser treatment {city} price",
        "coolsculpting {city} cost",
        "medical spa {city} pricing",
    ],
    "dental": [
        "teeth whitening {city} dentist price",
        "dental cleaning {city} cost",
    ],
    "photography": [
        "photography session {city} price",
        "headshot photographer {city} cost",
        "photo shoot {city} -groupon",
    ],
    "food_class": [
        "cooking class {city} price",
        "wine tasting {city} cost",
        "culinary class {city} -groupon",
    ],
    "tour": [
        "city tour {city} price",
        "boat tour {city} cost tickets",
        "walking tour {city} -groupon",
    ],
    "gym": [
        "gym membership {city} monthly price",
        "personal trainer {city} cost per session",
        "fitness center {city} -groupon",
    ],
    # Online courses are location-independent — search by subject, not city.
    "online_course": [
        "{service} online course price -groupon -livingsocial",
        "{service} certification cost online",
        "best online {service} course price -groupon",
    ],
    "general": [
        "{service} {city} price cost -groupon -livingsocial",
        "best {service} {city} -groupon",
        "{service} {city} pricing book",
    ],
}


# Maps category key → clean human-readable service term (mirrors category_benchmarker)
_CLEAN_SERVICE_TERMS: dict[str, str] = {
    "juice_cleanse": "juice cleanse",
    "facial": "facial treatment",
    "massage": "massage therapy",
    "spa": "day spa",
    "fitness_class": "fitness class",
    "hair_salon": "hair salon",
    "nails": "nail salon",
    "waxing": "waxing service",
    "escape_room": "escape room",
    "bowling": "bowling",
    "movie_ticket": "movie ticket",
    "restaurant": "restaurant dining",
    "automotive": "oil change",
    "car_wash": "car detailing",
    "aesthetics": "aesthetic treatment",
    "dental": "dental service",
    "photography": "photography session",
    "food_class": "cooking class",
    "tour": "city tour",
    "gym": "gym membership",
    "online_course": "online certification course",
    "general": "service",
}


# Marketing / pricing boilerplate to strip when deriving a clean product term
# from a raw deal title (used for the generic + online_course fallbacks).
_BOILERPLATE_RE = re.compile(
    r"(\$\s*\d[\d,.]*|\bup to\b|\d+%\s*off\b|\boff\b|\bsave\b|\bdiscount\b|"
    r"\bgroupon\b|\bdeal\b|\bget\b|\bfrom\b|\bonly\b|\bnew\b|\bsale\b)",
    re.IGNORECASE,
)


def _clean_service_type(service_type: str) -> str:
    """
    Reduce a raw Groupon title to a concise, searchable product phrase.

    Strips pricing/marketing boilerplate ("Up to 50% Off", "$29.99", "Get",
    etc.) and a leading marketing prefix before a colon, then keeps the first
    few meaningful words. This is what makes the generic fallback search for the
    ACTUAL product (e.g. "clinical medical technician ekg course") instead of
    the literal word "service" — which previously pulled in unrelated
    (automotive) results.
    """
    text = (service_type or "").lower()
    # Drop a leading marketing prefix like "get hospital-ready: ..."
    if ":" in text:
        head, _, tail = text.partition(":")
        if len(head.split()) <= 4:
            text = tail
    text = _BOILERPLATE_RE.sub(" ", text)
    # Keep letters, numbers, & and / (so "ekg/ecg" survives); drop other punctuation.
    text = re.sub(r"[^a-z0-9&/\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    words = text.split()
    return " ".join(words[:6]).strip()


def _resolve_clean_service(service_type: str, category: str = "") -> tuple[str, str]:
    """
    Returns (cat_key, clean_service_term).
    The clean_service_term is what gets stored as CompetitorPrice.service_name
    and used for SKU confidence scoring — NOT the raw Groupon title.
    """
    cat_key = _detect_category(service_type, category)
    # For categories with no concrete service mapping (generic fallback) — and
    # for online courses, where the subject matters more than a generic label —
    # derive the search term from the actual product, never the literal "service".
    if cat_key in ("general", "online_course"):
        clean = _clean_service_type(service_type) or category or _CLEAN_SERVICE_TERMS[cat_key]
    else:
        clean = _CLEAN_SERVICE_TERMS.get(cat_key, category or service_type.split()[0])
    return cat_key, clean


def _build_category_queries(
    service_type: str,
    city: str,
    category: str = "",
) -> tuple[list[str], str]:
    """
    Build category-specific search queries.
    Returns (queries, clean_service_term).
    """
    cat_key, clean_service = _resolve_clean_service(service_type, category)
    templates = _QUERY_TEMPLATES.get(cat_key, _QUERY_TEMPLATES["general"])
    location = city.strip() if city.strip() else "near me"

    queries = []
    for tmpl in templates:
        q = (
            tmpl
            .replace("{service}", clean_service)
            .replace("{city}", location)
            .replace("{n}", "3")  # default for multi-day packages
        )
        queries.append(q)

    return queries, clean_service


# ---------------------------------------------------------------------------
# Domain validation
# ---------------------------------------------------------------------------

def _is_valid_competitor_domain(domain: str, page_title: str = "", page_text: str = "") -> tuple[bool, bool]:
    """
    Returns (is_valid, is_merchant).

    is_valid: True if the source might have competitor pricing
    is_merchant: True if it looks like a direct merchant booking page
    """
    clean = domain.lstrip("www.")

    # Hard reject
    if clean in _REJECT_DOMAINS:
        return False, False

    # Approved aggregators — valid but not "merchant"
    if clean in _APPROVED_AGGREGATORS:
        return True, False

    # Reject government / educational TLDs
    if clean.endswith(".gov") or clean.endswith(".edu"):
        return False, False

    # Reject informational/news signals in page title
    title_lower = (page_title or "").lower()
    reject_title_signals = [
        "how much does", "average cost of", "cost of living",
        "what is the average", "price guide", "salary",
        "wikipedia", "investopedia", "nerdwallet",
        "travel guide", "visit ", "tourism",
        "vs car payment", "depreciation", "msrp",
        "car insurance", "fuel cost",
    ]
    if any(sig in title_lower for sig in reject_title_signals):
        return False, False

    # Looks like a merchant if it has booking/pricing-related terms
    merchant_signals = [
        "book now", "book online", "schedule", "reserve", "appointment",
        "buy tickets", "purchase", "add to cart", "checkout",
        "pricing", "rates", "services", "menu", "packages",
        "per person", "per session", "per hour",
    ]
    text_lower = (page_text or "")[:2000].lower()
    merchant_score = sum(1 for sig in merchant_signals if sig in text_lower)
    is_merchant = merchant_score >= 3

    return True, is_merchant


# ---------------------------------------------------------------------------
# SKU match confidence
# ---------------------------------------------------------------------------

def _compute_sku_confidence(
    clean_service: str,
    page_title: str,
    page_text: str,
    is_merchant: bool,
) -> float:
    """
    Estimate how likely this page is selling the same service/product.

    Uses the CLEAN service term (e.g., "juice cleanse"), NOT the raw Groupon
    title — the Groupon title contains brand names and boilerplate that share
    zero overlap with competitor pages, producing falsely low confidence.

    Returns 0.0–1.0.
    """
    if not is_merchant:
        return 0.25  # info/aggregator pages don't have a direct product match

    # Use the clean category term for matching
    service_words = set(re.findall(r"\b\w{3,}\b", clean_service.lower()))
    title_words = set(re.findall(r"\b\w{3,}\b", (page_title or "").lower()))
    text_words = set(re.findall(r"\b\w{3,}\b", (page_text or "")[:2000].lower()))

    if not service_words:
        return 0.4

    # Overlap with page title (strongest signal — title is curated)
    title_overlap = len(service_words & title_words) / len(service_words)
    # Overlap with page body
    text_overlap = min(len(service_words & text_words) / len(service_words), 1.0)

    confidence = (title_overlap * 0.65) + (text_overlap * 0.35)

    if is_merchant and text_overlap > 0.4:
        confidence = min(confidence + 0.15, 1.0)

    return round(confidence, 2)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def find_competitors(
    service_type: str,
    city: str,
    groupon_price: Optional[float] = None,
    merchant_name: Optional[str] = None,
    category: str = "",
    max_competitors: int = 5,
) -> list[CompetitorPrice]:
    """
    Find competitor businesses and pricing for a service in a city.

    Uses category-specific query templates and confidence scoring.
    Only returns sources that pass domain validation.
    """
    queries, clean_service = _build_category_queries(service_type, city, category)
    search_results: list[SearchResult] = []

    for query in queries:
        results = await search(query, num_results=6)
        search_results.extend(results)
        await asyncio.sleep(1.0)

    # Deduplicate by domain, validate each
    candidate_urls = _deduplicate_and_validate(search_results)

    # Scrape prices with confidence scoring
    competitors: list[CompetitorPrice] = []
    tasks = [
        _scrape_competitor(
            url, clean_service, groupon_price,
            merchant_name=merchant_name,
        )
        for url in candidate_urls[:max_competitors + 3]
    ]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    for r in raw_results:
        if isinstance(r, CompetitorPrice):
            competitors.append(r)
            if len(competitors) >= max_competitors:
                break

    cat_key, _ = _resolve_clean_service(service_type, category)
    log.info(
        "Found %d valid competitors for %r → category=%s, clean_service=%r in %s",
        len(competitors), service_type, cat_key, clean_service, city,
    )
    return competitors


def _deduplicate_and_validate(results: list[SearchResult]) -> list[str]:
    """Return unique URLs, one per domain, skipping rejected domains."""
    seen_domains: set[str] = set()
    urls: list[str] = []

    for r in results:
        if not r.url.startswith("http"):
            continue
        domain = urlparse(r.url).netloc.lower().lstrip("www.")
        if domain in seen_domains:
            continue

        # Quick reject based on domain alone (before fetching the page)
        clean = domain.lstrip("www.")
        if clean in _REJECT_DOMAINS:
            log.debug("Rejected domain (blocklist): %s", domain)
            continue
        if clean.endswith(".gov") or clean.endswith(".edu"):
            log.debug("Rejected domain (gov/edu): %s", domain)
            continue

        seen_domains.add(domain)
        urls.append(r.url)

    return urls


async def _scrape_competitor(
    url: str,
    clean_service: str,
    groupon_price: Optional[float],
    merchant_name: Optional[str] = None,
) -> Optional[CompetitorPrice]:
    """
    Fetch a competitor page, validate it, extract price + confidence score.

    clean_service: the category-derived human-readable service name
      (e.g. "juice cleanse", not the full Groupon title).
      Used for both service_name in the result and SKU confidence scoring.
    """
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
    page_title = _extract_title_text(soup, url)

    # Validate domain + page content
    domain = urlparse(url).netloc.lower().lstrip("www.")
    is_valid, is_merchant = _is_valid_competitor_domain(domain, page_title, page_text)

    if not is_valid:
        log.debug("Rejected page (content validation): %s — title: %s", url, page_title[:60])
        return None

    name = page_title or domain.split(".")[0].title()
    if not name:
        return None

    # Check if this URL is the merchant's own website (direct booking candidate)
    is_own_site = False
    if merchant_name:
        merchant_words = set(re.findall(r"\b\w{4,}\b", merchant_name.lower()))
        if merchant_words:
            domain_match_count = sum(1 for w in merchant_words if w in domain)
            threshold = max(2, round(len(merchant_words) * 0.5))
            if domain_match_count >= threshold:
                is_own_site = True

    # Compute SKU match confidence using the CLEAN service term (not raw Groupon title)
    confidence = _compute_sku_confidence(clean_service, page_title, page_text, is_merchant)

    # Extract prices from page text
    prices = _extract_prices(page_text, clean_service)

    # Build notes
    if is_own_site:
        notes = "DIRECT_BOOKING_CANDIDATE — use for groupon_vs_direct_savings"
    elif not prices:
        notes = "Price not found on page"
    else:
        notes = None

    if not prices and not is_own_site:
        if groupon_price and confidence >= 0.5:
            return CompetitorPrice(
                competitor_name=name,
                service_name=clean_service,  # clean term, not raw Groupon title
                source_url=url,
                notes=notes or "Price not found on page",
                sku_match_confidence=confidence,
                is_merchant=is_merchant,
            )
        return None

    regular_price = prices[0] if prices else None
    sale_price = min(prices) if len(prices) > 1 and min(prices) != regular_price else None

    return CompetitorPrice(
        competitor_name=name,
        service_name=clean_service,  # clean term, not raw Groupon title
        regular_price=regular_price,
        sale_price=sale_price,
        source_url=url,
        notes=notes,
        sku_match_confidence=confidence,
        is_merchant=is_merchant,
    )


def _extract_title_text(soup: BeautifulSoup, url: str) -> str:
    """Extract page title for validation and naming."""
    og_site = soup.find("meta", property="og:site_name")
    if og_site and og_site.get("content"):
        return og_site["content"].strip()[:80]

    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)[:80]

    title = soup.find("title")
    if title:
        text = title.get_text(strip=True)
        text = re.split(r"\s*[|\-–]\s*", text)[0].strip()
        return text[:80]

    domain = urlparse(url).netloc.lstrip("www.")
    return domain.split(".")[0].title()


def _extract_prices(page_text: str, service_type: str) -> list[float]:
    """
    Extract price mentions from page text plausibly related to the service.
    Returns prices sorted descending (highest first, as likely regular price).
    """
    all_prices = []
    for m in _PRICE_RE.findall(page_text):
        try:
            price = float(m.replace(",", ""))
            if 3 <= price <= 2000:  # reasonable service price range
                all_prices.append(price)
        except ValueError:
            pass

    if not all_prices:
        return []

    unique = sorted(set(all_prices), reverse=True)
    return unique[:3]
