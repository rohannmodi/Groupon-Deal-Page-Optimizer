"""
Trust & urgency parser.

Trust signals:  ratings, review counts, "X bought", money-back guarantees, badges.
Urgency elements: countdown timers, "selling fast", limited quantity, expiry text.

These are deliberately kept separate from the deal_parser because they feed
directly into the AI audit scoring as distinct categories.
"""

from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup, Tag

from models import TrustSignal, UrgencyElement


# ---------------------------------------------------------------------------
# Trust signals
# ---------------------------------------------------------------------------

def parse_trust_signals(soup: BeautifulSoup) -> list[TrustSignal]:
    signals: list[TrustSignal] = []

    # 1. Star rating
    rating = _extract_rating(soup)
    if rating:
        signals.append(TrustSignal(signal_type="rating", signal_value=rating))

    # 2. Review count
    review_count = _extract_review_count(soup)
    if review_count:
        signals.append(TrustSignal(signal_type="review_count", signal_value=review_count))

    # 3. "X bought" / "X purchased" social proof
    sold = _extract_sold_count(soup)
    if sold:
        signals.append(TrustSignal(signal_type="sold_count", signal_value=sold))

    # 4. Money-back guarantee
    if _has_guarantee(soup):
        signals.append(
            TrustSignal(signal_type="guarantee", signal_value="Groupon Promise / money-back guarantee")
        )

    # 5. Any explicit badge text
    badges = _extract_badges(soup)
    signals.extend(badges)

    return signals


def _extract_rating(soup: BeautifulSoup) -> Optional[str]:
    # data-qa first
    el = soup.find(attrs={"data-qa": "rating-value"}) or soup.find(attrs={"data-qa": "deal-rating"})
    if el:
        return _clean_text(el)

    # aria-label on star elements: "4.7 out of 5"
    star_el = soup.find(attrs={"aria-label": re.compile(r"\d+\.?\d*\s+out of\s+\d+", re.I)})
    if star_el:
        m = re.search(r"(\d+\.?\d*)\s+out of", star_el.get("aria-label", ""), re.I)
        if m:
            return m.group(1)

    # class-based
    el = soup.select_one("[class*='rating-value'], [class*='RatingValue'], [class*='stars-value']")
    if el:
        return _clean_text(el)

    # Look for a number like "4.7" near a star icon
    for candidate in soup.find_all(class_=re.compile(r"rating|star", re.I)):
        text = candidate.get_text(strip=True)
        m = re.match(r"^(\d\.\d)$", text)
        if m:
            return m.group(1)

    return None


def _extract_review_count(soup: BeautifulSoup) -> Optional[str]:
    el = (
        soup.find(attrs={"data-qa": "review-count"})
        or soup.select_one("[class*='review-count'], [class*='ReviewCount']")
    )
    if el:
        return _clean_text(el)

    # Pattern: "(1,234 Ratings)" or "1,234 reviews"
    text = soup.get_text()
    m = re.search(r"\(?(\d[\d,]*)\s+(?:rating|review)s?\)?", text, re.I)
    if m:
        return f"{m.group(1)} ratings"

    return None


def _extract_sold_count(soup: BeautifulSoup) -> Optional[str]:
    el = (
        soup.find(attrs={"data-qa": "sold-count"})
        or soup.find(attrs={"data-qa": "units-sold"})
        or soup.select_one("[class*='sold-count'], [class*='SoldCount'], [class*='units-sold']")
    )
    if el:
        return _clean_text(el)

    # Pattern: "10,000+ bought" / "500 purchased"
    text = soup.get_text()
    m = re.search(
        r"([\d,]+\+?)\s+(?:bought|purchased|sold|redeemed)",
        text,
        re.I,
    )
    if m:
        return f"{m.group(1)} bought"

    return None


def _has_guarantee(soup: BeautifulSoup) -> bool:
    text = soup.get_text().lower()
    return any(kw in text for kw in ("groupon promise", "money-back", "refund guarantee", "satisfaction guaranteed"))


def _extract_badges(soup: BeautifulSoup) -> list[TrustSignal]:
    badges = []
    badge_els = soup.select("[class*='badge'], [class*='Badge'], [class*='seal'], [data-qa*='badge']")
    for el in badge_els:
        text = _clean_text(el)
        if text and len(text) < 80:
            badges.append(TrustSignal(signal_type="badge", signal_value=text))
    return badges


# ---------------------------------------------------------------------------
# Urgency elements
# ---------------------------------------------------------------------------

def parse_urgency_elements(soup: BeautifulSoup) -> list[UrgencyElement]:
    elements: list[UrgencyElement] = []

    # 1. Countdown timer
    countdown = _extract_countdown(soup)
    if countdown:
        elements.append(countdown)

    # 2. Limited quantity
    qty = _extract_limited_qty(soup)
    if qty:
        elements.append(qty)

    # 3. "Selling fast" or "almost gone"
    fast = _extract_selling_fast(soup)
    if fast:
        elements.append(fast)

    # 4. Expiry / ends date
    ends = _extract_ends_text(soup)
    if ends:
        elements.append(ends)

    return elements


def _extract_countdown(soup: BeautifulSoup) -> Optional[UrgencyElement]:
    el = (
        soup.find(attrs={"data-qa": "countdown-timer"})
        or soup.select_one("[class*='countdown'], [class*='Countdown'], [class*='timer']")
    )
    if el:
        text = _clean_text(el) or "Countdown timer present"
        return UrgencyElement(element_type="countdown", element_text=text, is_visible_atf=True)
    return None


def _extract_limited_qty(soup: BeautifulSoup) -> Optional[UrgencyElement]:
    text = soup.get_text()
    m = re.search(
        r"(only\s+\d+\s+(?:left|remaining)|limited\s+(?:quantity|availability|time)|\d+\s+(?:spots?|vouchers?)\s+(?:left|remaining))",
        text,
        re.I,
    )
    if m:
        return UrgencyElement(
            element_type="limited_qty",
            element_text=m.group(0).strip(),
            is_visible_atf=False,
        )
    return None


def _extract_selling_fast(soup: BeautifulSoup) -> Optional[UrgencyElement]:
    el = soup.find(string=re.compile(r"selling\s+fast|almost\s+gone|going\s+fast|high\s+demand", re.I))
    if el:
        return UrgencyElement(
            element_type="selling_fast",
            element_text=el.strip(),
            is_visible_atf=False,
        )
    return None


def _extract_ends_text(soup: BeautifulSoup) -> Optional[UrgencyElement]:
    el = soup.find(string=re.compile(r"(ends?|expires?|valid\s+through|buy\s+by)\s+(on\s+)?\w+", re.I))
    if el:
        return UrgencyElement(
            element_type="ends_text",
            element_text=el.strip(),
            is_visible_atf=False,
        )
    return None


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------

def parse_reviews(soup: BeautifulSoup) -> tuple[Optional[int], Optional[float], list]:
    """
    Return (review_count, avg_rating, sample_reviews).
    sample_reviews is a list of dicts: {author, rating, text, date}.
    """
    from models import ReviewSample

    count: Optional[int] = None
    avg: Optional[float] = None
    samples: list[ReviewSample] = []

    # Count from text
    m = re.search(r"([\d,]+)\s+(?:rating|review)s?", soup.get_text(), re.I)
    if m:
        try:
            count = int(m.group(1).replace(",", ""))
        except ValueError:
            pass

    # Average rating
    rating_str = _extract_rating(soup)
    if rating_str:
        try:
            avg = float(rating_str)
        except ValueError:
            pass

    # Sample reviews
    review_containers = soup.select(
        "[data-qa='review-item'], [class*='ReviewItem'], [class*='review-item'], "
        "[class*='Review']:not([class*='review-count']):not([class*='review-summary'])"
    )
    for container in review_containers[:5]:  # cap at 5 samples
        author_el = container.select_one(
            "[class*='author'], [class*='Author'], [data-qa='review-author']"
        )
        date_el = container.select_one("[class*='date'], [class*='Date'], time")
        rating_el = container.find(attrs={"aria-label": re.compile(r"\d+ (?:out of|star)", re.I)})
        text_el = container.select_one(
            "[class*='review-text'], [class*='ReviewText'], [data-qa='review-text'], p"
        )

        author = _clean_text(author_el)
        date = _clean_text(date_el)
        text = _clean_text(text_el)

        review_rating: Optional[float] = None
        if rating_el:
            m2 = re.search(r"(\d+(?:\.\d+)?)\s*(?:out of|star)", rating_el.get("aria-label", ""), re.I)
            if m2:
                review_rating = float(m2.group(1))

        if text:
            samples.append(ReviewSample(author=author, rating=review_rating, text=text, date=date))

    return count, avg, samples


# ---------------------------------------------------------------------------
# Shared util
# ---------------------------------------------------------------------------

def _clean_text(el: Tag | None) -> Optional[str]:
    if el is None:
        return None
    t = el.get_text(strip=True) if hasattr(el, "get_text") else str(el).strip()
    return t if t else None
