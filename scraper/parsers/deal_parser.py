"""
Core deal parser — title, merchant, category, city, description, highlights, fine print, FAQs.

Strategy:
  1. JSON-LD schemas for FAQs and structured data (most reliable on Groupon)
  2. data-qa attributes (Groupon's stable QA targeting)
  3. Class-based and structural CSS selectors (fallback)

Never raise on a miss — return None / [].
"""

from __future__ import annotations

import json as _json
import re
from typing import Optional

from bs4 import BeautifulSoup, Tag

from models import FAQ


# ---------------------------------------------------------------------------
# Geographic location validation
# ---------------------------------------------------------------------------

# Two-letter US state abbreviations (upper- or lower-case after stripping)
_US_STATES = {
    "al","ak","az","ar","ca","co","ct","de","fl","ga","hi","id","il","in",
    "ia","ks","ky","la","me","md","ma","mi","mn","ms","mo","mt","ne","nv",
    "nh","nj","nm","ny","nc","nd","oh","ok","or","pa","ri","sc","sd","tn",
    "tx","ut","vt","va","wa","wv","wi","wy","dc",
}

# If a candidate city string contains any of these it's almost certainly not
# a city name — it's course copy, a date string, a sentence fragment, etc.
_NOT_CITY_SIGNALS = re.compile(
    r"\b(month|week|day|year|course|enrollment|starting|from|your|per|date|"
    r"purchase|voucher|valid|expires?|redeemable|redeem|subject|prior|book|"
    r"appointment|advance|required|included|exclud|limit|maximum|minimum|"
    r"must|cannot|please|contact|call|visit|online|website|http|www)\b",
    re.IGNORECASE,
)


def _is_valid_city(text: str) -> bool:
    """
    Return True if `text` looks like a city name rather than page copy.

    Valid city: 1–4 words, only letters/spaces/hyphens/apostrophes/periods,
    no sentence-fragment signals, ≤ 30 chars total.
    """
    if not text or len(text) > 30:
        return False
    if _NOT_CITY_SIGNALS.search(text):
        return False
    # Must be only word-characters, spaces, hyphens, apostrophes, periods
    if not re.match(r"^[A-Za-z][A-Za-z\s\-\'\.]{0,29}$", text):
        return False
    # Must be 1–4 words
    if not (1 <= len(text.split()) <= 4):
        return False
    return True


def _is_valid_state(text: str) -> bool:
    """Return True if text is a recognisable US state abbreviation or name."""
    if not text:
        return False
    t = text.strip().lower()
    return t in _US_STATES or len(t) == 2 and t.isalpha()


# ---------------------------------------------------------------------------
# Known non-city / non-merchant values to filter out
# ---------------------------------------------------------------------------

_EXCLUDED_MERCHANTS = {
    "sell on groupon", "groupon", "see all deals", "shop", "deals",
    "local", "things to do", "all deals", "home", "nearby", "categories",
}

_EXCLUDED_CITY_BREADCRUMBS = {
    "home", "deals", "local", "things to do", "all deals", "nearby",
    "categories", "health & beauty", "health and beauty", "food & drink",
    "food and drink", "shopping", "travel", "automotive", "sports",
    "entertainment", "activities", "kids", "family", "spa", "wellness",
    "restaurants", "beauty", "fitness", "services", "experiences",
    "yoga", "massage", "pilates", "hair", "nails", "skin care",
    "bowling", "golf", "hiking", "cycling", "swimming",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_title(soup: BeautifulSoup) -> Optional[str]:
    return (
        _text(soup.find(attrs={"data-qa": "deal-page-title"}))
        or _text(soup.find("h1"))
        or _text(soup.select_one(".deal-title, .title-section h1, [class*='DealTitle']"))
    )


def parse_subtitle(soup: BeautifulSoup) -> Optional[str]:
    return (
        _text(soup.find(attrs={"data-qa": "deal-page-subtitle"}))
        or _text(soup.select_one(".deal-subtitle, .subtitle, [class*='Subtitle']"))
    )


def parse_merchant_name(soup: BeautifulSoup) -> Optional[str]:
    """
    Extract the merchant / business name.

    Groupon often shows a "Sell on Groupon" nav link and a "About <Merchant>"
    H2 near the bottom — we want the actual merchant. We try structured
    data-qa elements first, then filter any nav/footer results.
    """
    # Highest confidence: structured data-qa
    for qa in ("merchant-name", "soldby", "provider-name", "business-name"):
        el = soup.find(attrs={"data-qa": qa})
        t = _text(el)
        if t and t.lower() not in _EXCLUDED_MERCHANTS:
            return t

    # "About <Merchant>" H2 — typically the cleanest source
    for heading in soup.find_all(["h2", "h3"]):
        ht = _text(heading) or ""
        if ht.lower().startswith("about "):
            merchant = ht[6:].strip()
            if merchant and merchant.lower() not in _EXCLUDED_MERCHANTS:
                return merchant

    # Class-based selectors
    for sel in (
        "[class*='MerchantName']",
        "[class*='merchant-name']",
        "[class*='ProviderName']",
        "[class*='BusinessName']",
        ".soldby",
        ".merchant-name",
    ):
        t = _text(soup.select_one(sel))
        if t and t.lower() not in _EXCLUDED_MERCHANTS:
            return t

    # Schema.org JSON-LD name field (often the Provider/Performer)
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = _json.loads(script.string or "")
            if isinstance(data, list):
                data = data[0] if data else {}
            if isinstance(data, dict):
                for key in ("name", "provider", "performer", "organizer"):
                    val = data.get(key)
                    if isinstance(val, str) and val.lower() not in _EXCLUDED_MERCHANTS:
                        return val
                    if isinstance(val, dict):
                        n = val.get("name", "")
                        if n and n.lower() not in _EXCLUDED_MERCHANTS:
                            return n
        except Exception:
            continue

    return None


def parse_category(soup: BeautifulSoup) -> Optional[str]:
    """Extract category from breadcrumbs (last meaningful crumb before the deal title)."""
    breadcrumbs = soup.select(
        "nav[aria-label*='breadcrumb'] a, .breadcrumb a, [class*='Breadcrumb'] a, "
        "[aria-label*='breadcrumb'] li, ol[class*='breadcrumb'] li"
    )
    # Try the last 2-3 crumbs (closest to the deal level = most specific)
    for bc in reversed(breadcrumbs):
        t = _text(bc) or ""
        if (
            2 <= len(t) <= 40
            and "groupon" not in t.lower()
            and t.lower() not in _EXCLUDED_CITY_BREADCRUMBS
        ):
            return t

    # Try meta keywords
    meta_kw = soup.find("meta", attrs={"name": "keywords"})
    if meta_kw and meta_kw.get("content"):
        return meta_kw["content"].split(",")[0].strip()

    return None


def parse_city_state(soup: BeautifulSoup, url: str) -> tuple[Optional[str], Optional[str]]:
    """Extract city and state from the page or URL."""

    # 1. Structured location element
    location_el = (
        soup.find(attrs={"data-qa": "deal-location"})
        or soup.select_one(".location, [class*='Location'], [class*='city']")
    )
    if location_el:
        text = _text(location_el) or ""
        parts = [p.strip() for p in text.split(",")]
        city = parts[0] if parts else None
        state = parts[1] if len(parts) > 1 else None
        if city and _is_valid_city(city) and city.lower() not in _EXCLUDED_CITY_BREADCRUMBS:
            return city, state

    # 2. Breadcrumbs — city is usually a short, title-case, geographically named crumb
    breadcrumbs = soup.select(
        "nav[aria-label*='breadcrumb'] a, .breadcrumb a, [class*='Breadcrumb'] a, "
        "[aria-label*='breadcrumb'] li, ol[class*='breadcrumb'] li"
    )
    for bc in breadcrumbs:
        bc_text = _text(bc) or ""
        if (
            "groupon" not in bc_text.lower()
            and bc_text.lower() not in _EXCLUDED_CITY_BREADCRUMBS
            and _is_valid_city(bc_text)
        ):
            return bc_text, None

    # 3. Open Graph / geo meta
    og_loc = soup.find("meta", property="og:locality") or soup.find("meta", attrs={"name": "geo.placename"})
    if og_loc and og_loc.get("content"):
        candidate = og_loc["content"].strip()
        if _is_valid_city(candidate):
            return candidate, None

    # 4. JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = _json.loads(script.string or "")
            if isinstance(data, list):
                data = data[0] if data else {}
            if isinstance(data, dict):
                loc = data.get("location") or data.get("areaServed") or {}
                if isinstance(loc, dict):
                    city = loc.get("addressLocality") or loc.get("name")
                    state = loc.get("addressRegion")
                    if city and _is_valid_city(city) and city.lower() not in _EXCLUDED_CITY_BREADCRUMBS:
                        return city, state
        except Exception:
            continue

    # 5. URL: /deals/{city}/{slug} (two-segment path → city is first segment)
    m = re.search(r"groupon\.com/(?:deals|local)/([a-z-]+)/([a-z0-9-]+)", url)
    if m:
        city_slug = m.group(1).replace("-", " ").title()
        if city_slug.lower() not in _EXCLUDED_CITY_BREADCRUMBS and _is_valid_city(city_slug):
            return city_slug, None

    # 6. Slug-embedded city for single-segment URLs like /deals/ga-great-clips-chicago
    _CITY_SLUGS: list[tuple[str, str]] = [
        ("chicago", "Chicago"), ("new-york", "New York"), ("new-york-city", "New York"),
        ("nyc", "New York"), ("los-angeles", "Los Angeles"), ("houston", "Houston"),
        ("phoenix", "Phoenix"), ("philadelphia", "Philadelphia"),
        ("san-antonio", "San Antonio"), ("san-diego", "San Diego"),
        ("dallas", "Dallas"), ("san-jose", "San Jose"), ("austin", "Austin"),
        ("seattle", "Seattle"), ("denver", "Denver"), ("boston", "Boston"),
        ("nashville", "Nashville"), ("miami", "Miami"), ("atlanta", "Atlanta"),
        ("las-vegas", "Las Vegas"), ("portland", "Portland"),
        ("minneapolis", "Minneapolis"), ("charlotte", "Charlotte"),
        ("washington", "Washington DC"), ("detroit", "Detroit"),
        ("memphis", "Memphis"), ("louisville", "Louisville"),
        ("baltimore", "Baltimore"), ("milwaukee", "Milwaukee"),
        ("albuquerque", "Albuquerque"), ("tucson", "Tucson"),
        ("fresno", "Fresno"), ("sacramento", "Sacramento"),
        ("kansas-city", "Kansas City"), ("mesa", "Mesa"),
        ("omaha", "Omaha"), ("cleveland", "Cleveland"),
        ("raleigh", "Raleigh"), ("virginia-beach", "Virginia Beach"),
        ("colorado-springs", "Colorado Springs"), ("tampa", "Tampa"),
        ("new-orleans", "New Orleans"), ("pittsburgh", "Pittsburgh"),
        ("orlando", "Orlando"), ("cincinnati", "Cincinnati"),
        ("minneapolis", "Minneapolis"), ("st-louis", "St. Louis"),
        ("salt-lake-city", "Salt Lake City"), ("jacksonville", "Jacksonville"),
    ]
    url_lower = url.lower()
    for slug, city_name in _CITY_SLUGS:
        if f"-{slug}" in url_lower or url_lower.endswith(f"/{slug}"):
            # City slug lookup is already validated — these are known city names
            return city_name, None

    # 7. "Where To Redeem" section — Groupon shows redemption addresses for local deals.
    # (see also parse_redemption_address for full street-level extraction)
    # The section renders as: "[City]\n[distance] mi\n[Street Address], [City]"
    # We look for the first valid city name from the first address line.
    # Common pattern: "600 North Michigan Avenue, Chicago" → city = "Chicago"
    _ADDR_CITY_RE = re.compile(
        r",\s*([A-Z][a-zA-Z\s\-\.]{2,20})(?:\s+\d{5})?(?:\s*$|\n)",
        re.MULTILINE,
    )
    # Try id="whereToRedeem" first (Groupon's rendered tab section)
    redeem_el = soup.find(id="whereToRedeem") or soup.find(
        attrs={"data-qa": "where-to-redeem"}
    )
    if not redeem_el:
        # Fall back: find any element mentioning street-address-like text
        for heading in soup.find_all(["h2", "h3", "h4"]):
            if "where to redeem" in (_text(heading) or "").lower():
                redeem_el = heading.parent
                break
    if redeem_el:
        redeem_text = redeem_el.get_text(separator="\n", strip=True)
        for m_addr in _ADDR_CITY_RE.finditer(redeem_text):
            candidate = m_addr.group(1).strip()
            if candidate.lower() not in _EXCLUDED_CITY_BREADCRUMBS and _is_valid_city(candidate):
                return candidate, None

    return None, None


def parse_redemption_address(soup: BeautifulSoup) -> Optional[str]:
    """
    Extract the first full street address from the 'Where To Redeem' section.

    Groupon local service deals show redemption locations like:
      "600 North Michigan Avenue, Chicago"
      "38 West 32nd Street, New York"

    Returns None for Goods deals (no redemption address), nationwide/online
    deals, or when the only match is page copy rather than a real address.

    The street-type tokens are matched with word boundaries (``\\b``) so a
    suffix like "St" cannot match inside an unrelated word such as "Starting"
    — without this, prose like "2 months per course starting from your
    enrollment date..." was being mistaken for an address.
    """
    # Try id="whereToRedeem" (Groupon's rendered tab)
    redeem_el = soup.find(id="whereToRedeem") or soup.find(
        attrs={"data-qa": "where-to-redeem"}
    )
    if not redeem_el:
        for heading in soup.find_all(["h2", "h3", "h4"]):
            if "where to redeem" in (_text(heading) or "").lower():
                redeem_el = heading.parent
                break

    # Prefer the dedicated redemption section; otherwise fall back to the
    # merchant's own description prose (some merchants list a pickup/redemption
    # address there). We deliberately do NOT scan the whole page, which would
    # surface addresses from unrelated "similar deals" carousels. Either way
    # every candidate must pass _is_valid_address, so prose/review fragments
    # that merely start with a number are never accepted.
    if redeem_el:
        search_text = redeem_el.get_text(separator="\n", strip=True)
    else:
        search_text = parse_description(soup) or ""

    for m in _STREET_RE.finditer(search_text):
        candidate = m.group(1).strip()
        if _is_valid_address(candidate):
            return candidate
    return None


# Street tokens must be whole words (note the trailing \b after the group) so
# "St" doesn't match inside "Starting", "Dr" inside "Andrew", etc. The street
# name between the number and the suffix is limited to a few tokens so the
# match can't run across an entire sentence.
_STREET_RE = re.compile(
    r"\b(\d{1,6}\s+(?:[A-Za-z0-9][A-Za-z0-9.\-]*\s+){0,4}"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Way|"
    r"Court|Ct|Place|Pl|Parkway|Pkwy|Highway|Hwy|Square|Sq|Terrace|Ter)\b"
    r"(?:\s+(?:#|Ste\.?|Suite|Unit|Apt\.?|Fl\.?|Floor)\s*[A-Za-z0-9\-]+)?"
    r"\s*,\s*[A-Za-z][A-Za-z\s\.\-']{1,30})",
    re.IGNORECASE,
)


def _is_valid_address(text: str) -> bool:
    """
    Validate that an extracted string is a real street address and not a
    sentence fragment that happened to start with a number.
    """
    if not text or len(text) > 80:
        return False
    if "\n" in text:
        return False
    # Must start with a street number and contain a comma (street, city).
    if "," not in text or not re.match(r"^\d", text.strip()):
        return False
    # Reject prose: course copy, voucher terms, dates, etc.
    if _NOT_CITY_SIGNALS.search(text):
        return False
    # The portion after the final comma is the city — it must look like a place.
    city_part = text.rsplit(",", 1)[-1].strip()
    # Strip a trailing ZIP if present (e.g. "New York 10001").
    city_part = re.sub(r"\s+\d{5}(?:-\d{4})?$", "", city_part).strip()
    return _is_valid_city(city_part)


def parse_description(soup: BeautifulSoup) -> Optional[str]:
    # data-qa
    el = soup.find(attrs={"data-qa": "deal-description"})
    if el:
        return el.get_text(separator="\n", strip=True)

    # Primary strategy: collect text from all deal content sections by H2 heading
    # keywords. This is more reliable than id/class selectors on React pages where
    # CSS classes and IDs are unstable or used as scroll anchors only.
    # "id='about'" on Groupon is a small scroll-target anchor, not a content wrapper.
    body_sections = _extract_deal_content_sections(soup)
    if body_sections and len(body_sections) > 200:
        return body_sections

    # Class selectors fallback
    for sel in (
        "[class*='Description']",
        "[class*='description-section']",
        ".deal-description",
        "[class*='AboutSection']",
        "[class*='about-section']",
    ):
        el = soup.select_one(sel)
        if el:
            txt = el.get_text(separator="\n", strip=True)
            if txt and len(txt) > 50:
                return txt

    # "About <Merchant>" section text
    for heading in soup.find_all(["h2", "h3"]):
        ht = (_text(heading) or "").lower()
        if ht.startswith("about "):
            parent = heading.parent
            if parent and parent.name in ("section", "div", "article"):
                txt = parent.get_text(separator="\n", strip=True)
                txt = txt.replace(_text(heading) or "", "", 1).strip()
                if txt and len(txt) > 50:
                    return txt

    return None


# Keywords that identify deal content sections (vs. pricing or nav sections)
_DEAL_SECTION_KEYWORDS = frozenset({
    "what to expect", "what you get", "highlights", "available",
    "plan your visit", "how it works", "important information",
    "about this deal", "the experience", "your experience",
    "what's included", "what is included",
    # Escape room / activity specific
    "available escape rooms", "available rooms", "escape rooms",
    "rooms available", "choose your room",
    # Spa / wellness specific
    "available treatments", "available services", "services offered",
    # General
    "what we offer", "overview", "details", "the deal",
})

# H2 patterns that are pricing options, not content sections — skip these
_PRICING_H2_RE = re.compile(
    r"\b(for \d+|option \d+|per person|per group|weekday|any day)\b",
    re.IGNORECASE,
)


def _extract_deal_content_sections(soup: BeautifulSoup) -> Optional[str]:
    """
    Find deal content sections (What To Expect, Available Escape Rooms, etc.)
    by their H2 heading text and concatenate their content.

    This avoids reliance on unstable React class names or id attributes which
    are typically just small scroll-target anchors on Groupon pages.
    """
    sections: list[str] = []

    for h2 in soup.find_all("h2"):
        heading_text = (_text(h2) or "").strip()
        heading_lower = heading_text.lower()

        # Skip pricing-option headings like "Choice of Escape Room Weekday - For 2"
        if _PRICING_H2_RE.search(heading_text):
            continue

        # Check if this heading matches a known content section keyword
        is_content_section = any(kw in heading_lower for kw in _DEAL_SECTION_KEYWORDS)
        if not is_content_section:
            continue

        # Collect this heading + all following siblings until the next H2
        section_parts = [heading_text]
        for sibling in h2.next_siblings:
            if getattr(sibling, "name", None) == "h2":
                break  # reached the next section
            if hasattr(sibling, "get_text"):
                t = sibling.get_text(separator="\n", strip=True)
                if t:
                    section_parts.append(t)

        if section_parts:
            sections.append("\n".join(section_parts))

    return "\n\n".join(sections) if sections else None


def parse_highlights(soup: BeautifulSoup) -> list[str]:
    """Extract bullet points for 'What You Get' / Highlights section."""
    # The FAQ section is a common false-positive — explicitly exclude it
    faq_section = _find_faq_section(soup)

    # Try data-qa
    for qa in ("highlights-section", "whats-included", "what-you-get"):
        container = soup.find(attrs={"data-qa": qa})
        if container and container is not faq_section and not _is_child_of(container, faq_section):
            return _extract_list_items(container)

    # Class-based selectors
    for sel in (
        "[class*='Highlight']",
        "[class*='highlight']",
        "[class*='WhatYouGet']",
        "[class*='Inclusion']",
        "[class*='option-description']",
        ".option-description",
    ):
        container = soup.select_one(sel)
        if container and container is not faq_section and not _is_child_of(container, faq_section):
            result = _extract_list_items(container)
            if result:
                return result

    # Heading-based search — but only if it's outside the FAQ section
    container = _find_section_by_heading(
        soup,
        ["highlights", "what you get", "what's included", "what's included",
         "what we offer", "what you'll get"],
        exclude=faq_section,
    )
    if container:
        result = _extract_list_items(container)
        if result:
            return result

    # Last resort: find ANY <ul> with 3+ <li> items in the deal body section
    # (catches Groupon pages where highlights aren't under a semantic heading)
    # Skip very large lists (pricing option lists) and the FAQ section.
    for ul in soup.find_all("ul"):
        if _is_child_of(ul, faq_section):
            continue
        items = ul.find_all("li", recursive=False)
        if 3 <= len(items) <= 12:
            texts = [_text(li) for li in items if _text(li)]
            # Require items to be at least 15 chars each (not just short tags)
            if texts and all(len(t) >= 15 for t in texts):
                return texts

    return []


def parse_fine_print(soup: BeautifulSoup) -> list[str]:
    """Extract terms and conditions / fine print."""
    faq_section = _find_faq_section(soup)

    # Try data-qa
    for qa in ("fine-print-section", "fine-print", "terms-section", "need-to-know"):
        container = soup.find(attrs={"data-qa": qa})
        if container and not _is_child_of(container, faq_section):
            return _extract_list_items(container)

    # Groupon tab section: id="needToKnowInfo" is the rendered fine-print tab.
    # This is more reliable than class selectors on React pages.
    for section_id in ("needToKnowInfo", "need-to-know-info", "fineprint", "finePrint", "terms"):
        el = soup.find(id=section_id)
        if el and not _is_child_of(el, faq_section):
            items = _extract_list_items(el)
            if items:
                return items

    # Class-based
    for sel in (
        "[class*='FinePrint']",
        "[class*='fine-print']",
        ".fine-print",
        "[class*='Terms']",
        "[class*='Restrictions']",
    ):
        container = soup.select_one(sel)
        if container and not _is_child_of(container, faq_section):
            result = _extract_list_items(container)
            if result:
                return result

    # Heading-based — outside FAQ section only
    container = _find_section_by_heading(
        soup,
        ["fine print", "terms", "restrictions", "expiration", "need to know", "need to know info"],
        exclude=faq_section,
    )
    if container:
        return _extract_list_items(container)

    return []


def parse_faqs(soup: BeautifulSoup) -> list[FAQ]:
    """Extract FAQ pairs if present. Tries JSON-LD FAQPage first (most reliable)."""

    # 1. JSON-LD FAQPage schema — Groupon embeds this reliably
    faqs = _parse_faqs_from_jsonld(soup)
    if faqs:
        return faqs

    # 2. Structured HTML FAQ section
    faq_section = _find_faq_section(soup)
    if faq_section is None:
        return []

    # dt/dd pattern
    dts = faq_section.find_all("dt")
    dds = faq_section.find_all("dd")
    if dts and dds:
        return [
            FAQ(question=_text(q), answer=_text(a))
            for q, a in zip(dts, dds)
            if _text(q) and _text(a)
        ]

    # Heading + sibling paragraph pattern
    faqs_out: list[FAQ] = []
    for heading in faq_section.find_all(["h3", "h4", "strong", "b"]):
        q = _text(heading)
        nxt = heading.find_next_sibling()
        a = _text(nxt) if nxt else None
        if q and a:
            faqs_out.append(FAQ(question=q, answer=a))

    return faqs_out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _text(el: Tag | None) -> Optional[str]:
    """Return stripped text from a BS4 element, or None."""
    if el is None:
        return None
    t = el.get_text(strip=True)
    return t if t else None


def _extract_list_items(container: Tag) -> list[str]:
    """Extract <li> text, falling back to line-split of text content."""
    items = container.find_all("li")
    if items:
        return [t for li in items if (t := _text(li))]
    text = container.get_text(separator="\n", strip=True)
    return [line for line in text.splitlines() if line.strip()]


def _find_faq_section(soup: BeautifulSoup) -> Optional[Tag]:
    """Return the FAQ section container (used to exclude it from highlights/fine-print)."""
    el = (
        soup.find(attrs={"data-qa": "faq-section"})
        or soup.select_one("[class*='FAQ'], [class*='faq'], [id*='faq']")
    )
    if el:
        return el

    for heading in soup.find_all(["h2", "h3"]):
        ht = (_text(heading) or "").lower()
        if "faq" in ht or "frequently asked" in ht:
            parent = heading.parent
            if parent and parent.name in ("section", "div", "article"):
                return parent
    return None


def _is_child_of(el: Optional[Tag], ancestor: Optional[Tag]) -> bool:
    """Return True if el is a descendant of ancestor."""
    if el is None or ancestor is None:
        return False
    try:
        return ancestor in el.parents
    except Exception:
        return False


def _find_section_by_heading(
    soup: BeautifulSoup,
    keywords: list[str],
    exclude: Optional[Tag] = None,
) -> Optional[Tag]:
    """
    Find a content section whose heading contains one of the keywords.
    Skips any section that is inside `exclude`.
    """
    for heading in soup.find_all(["h2", "h3", "h4", "strong", "b"]):
        if exclude and _is_child_of(heading, exclude):
            continue
        heading_text = (_text(heading) or "").lower()
        if any(kw in heading_text for kw in keywords):
            parent = heading.parent
            if parent and parent.name in ("section", "div", "article"):
                return parent
            sibling = heading.find_next_sibling()
            if sibling:
                return sibling
    return None


def _parse_faqs_from_jsonld(soup: BeautifulSoup) -> list[FAQ]:
    """Extract FAQs from JSON-LD FAQPage schema."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            raw = script.string or ""
            data = _json.loads(raw)

            # Handle array of schemas
            items_to_check = data if isinstance(data, list) else [data]
            for item in items_to_check:
                if not isinstance(item, dict):
                    continue
                if item.get("@type") != "FAQPage":
                    continue
                faqs: list[FAQ] = []
                for entity in item.get("mainEntity", []):
                    if not isinstance(entity, dict):
                        continue
                    q = entity.get("name", "").strip()
                    answer_obj = entity.get("acceptedAnswer", {})
                    a = (answer_obj.get("text", "") if isinstance(answer_obj, dict) else "").strip()
                    if q and a:
                        faqs.append(FAQ(question=q, answer=a))
                if faqs:
                    return faqs
        except Exception:
            continue
    return []
