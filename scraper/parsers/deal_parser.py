"""
Core deal parser — title, merchant, category, city, description, highlights, fine print, FAQs.

Strategy: try the most specific selectors first (data-qa attributes, which
Groupon uses for stable QA targeting), then fall back to class-based and
structural selectors. Never raise on a miss — return None / [].
"""

from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup, Tag

from models import FAQ


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
    # Groupon usually has merchant name as a link near the top
    candidates = [
        soup.find(attrs={"data-qa": "merchant-name"}),
        soup.find(attrs={"data-qa": "soldby"}),
        soup.select_one(".merchant-name, .soldby, [class*='MerchantName'], [class*='merchant']"),
        soup.select_one("a[href*='/biz/'], a[href*='merchant']"),
    ]
    for el in candidates:
        t = _text(el)
        if t:
            return t
    return None


def parse_category(soup: BeautifulSoup) -> Optional[str]:
    """Extract category from breadcrumbs or meta tags."""
    # Try breadcrumb
    breadcrumbs = soup.select(
        "nav[aria-label*='breadcrumb'] a, .breadcrumb a, [class*='Breadcrumb'] a"
    )
    if len(breadcrumbs) >= 2:
        # Usually: Home > Category > Subcategory > Deal
        return _text(breadcrumbs[-2]) or _text(breadcrumbs[1])

    # Try meta keywords
    meta_kw = soup.find("meta", attrs={"name": "keywords"})
    if meta_kw and meta_kw.get("content"):
        return meta_kw["content"].split(",")[0].strip()

    return None


def parse_city_state(soup: BeautifulSoup, url: str) -> tuple[Optional[str], Optional[str]]:
    """Extract city and state from the page or URL."""
    # Try structured location element
    location_el = (
        soup.find(attrs={"data-qa": "deal-location"})
        or soup.select_one(".location, [class*='Location'], [class*='city']")
    )
    if location_el:
        text = _text(location_el) or ""
        parts = [p.strip() for p in text.split(",")]
        city = parts[0] if parts else None
        state = parts[1] if len(parts) > 1 else None
        return city, state

    # Fall back to URL parsing: groupon.com/deals/city-name/...
    m = re.search(r"groupon\.com/(?:deals|local)/([a-z-]+)", url)
    if m:
        city_slug = m.group(1).replace("-", " ").title()
        return city_slug, None

    return None, None


def parse_description(soup: BeautifulSoup) -> Optional[str]:
    el = (
        soup.find(attrs={"data-qa": "deal-description"})
        or soup.select_one(
            ".deal-description, [class*='Description'], [class*='description-section']"
        )
    )
    if el:
        return el.get_text(separator="\n", strip=True)
    return None


def parse_highlights(soup: BeautifulSoup) -> list[str]:
    """Extract 'The Fine Print' style bullet list of what you get."""
    # Try data-qa
    container = (
        soup.find(attrs={"data-qa": "highlights-section"})
        or soup.find(attrs={"data-qa": "whats-included"})
        or _find_section_by_heading(soup, ["highlights", "what you get", "what's included"])
        or soup.select_one(
            "[class*='Highlight'], [class*='highlight'], .option-description"
        )
    )
    if container is None:
        return []

    items = container.find_all("li")
    if not items:
        # Some pages use <p> or <br>-separated text
        text = container.get_text(separator="\n", strip=True)
        return [line for line in text.splitlines() if line.strip()]

    return [_text(li) for li in items if _text(li)]


def parse_fine_print(soup: BeautifulSoup) -> list[str]:
    """Extract terms and conditions / fine print."""
    container = (
        soup.find(attrs={"data-qa": "fine-print-section"})
        or soup.find(attrs={"data-qa": "fine-print"})
        or _find_section_by_heading(soup, ["fine print", "terms", "restrictions", "expiration"])
        or soup.select_one("[class*='FinePrint'], [class*='fine-print'], .fine-print")
    )
    if container is None:
        return []

    items = container.find_all("li")
    if items:
        return [_text(li) for li in items if _text(li)]

    text = container.get_text(separator="\n", strip=True)
    return [line.strip() for line in text.splitlines() if line.strip()]


def parse_faqs(soup: BeautifulSoup) -> list[FAQ]:
    """Extract FAQ pairs if present."""
    faqs: list[FAQ] = []

    # Look for structured FAQ section
    faq_section = (
        soup.find(attrs={"data-qa": "faq-section"})
        or _find_section_by_heading(soup, ["faq", "frequently asked", "questions"])
        or soup.select_one("[class*='FAQ'], [class*='faq']")
    )
    if faq_section is None:
        return faqs

    # Try <dt>/<dd> pattern
    dts = faq_section.find_all("dt")
    dds = faq_section.find_all("dd")
    if dts and dds:
        for q, a in zip(dts, dds):
            qt = _text(q)
            at = _text(a)
            if qt and at:
                faqs.append(FAQ(question=qt, answer=at))
        return faqs

    # Try alternating heading / paragraph pattern
    headings = faq_section.find_all(["h3", "h4", "strong", "b"])
    for heading in headings:
        q = _text(heading)
        next_el = heading.find_next_sibling()
        a = _text(next_el) if next_el else None
        if q and a:
            faqs.append(FAQ(question=q, answer=a))

    return faqs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _text(el: Tag | None) -> Optional[str]:
    """Return stripped text from a BS4 element, or None."""
    if el is None:
        return None
    t = el.get_text(strip=True)
    return t if t else None


def _find_section_by_heading(
    soup: BeautifulSoup, keywords: list[str]
) -> Optional[Tag]:
    """
    Find a content section whose preceding heading contains one of the keywords.
    Returns the section container (parent or sibling of the heading).
    """
    for heading in soup.find_all(["h2", "h3", "h4", "strong", "b"]):
        heading_text = (heading.get_text() or "").lower()
        if any(kw in heading_text for kw in keywords):
            # Return the parent section or the next sibling
            parent = heading.parent
            if parent and parent.name in ("section", "div", "article"):
                return parent
            sibling = heading.find_next_sibling()
            if sibling:
                return sibling
    return None
