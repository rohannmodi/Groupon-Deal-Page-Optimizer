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

    # Broader search: any standalone "X.X" number (4.0–5.0 range) in visible text
    # near "rating", "stars", or "reviews" — skip script/style tags
    _RATING_CONTEXT_RE = re.compile(r"rating|star|review", re.I)
    _RATING_VALUE_RE = re.compile(r"\b([4-5]\.\d)\b")
    for el in soup.find_all(string=_RATING_VALUE_RE):
        if el.parent and el.parent.name in ("script", "style", "noscript"):
            continue
        m = _RATING_VALUE_RE.search(str(el))
        if m:
            # Check surrounding context for rating-related words
            parent_text = el.parent.get_text() if el.parent else ""
            if _RATING_CONTEXT_RE.search(parent_text) or _RATING_CONTEXT_RE.search(
                str(el.parent.get("class", "")) if el.parent else ""
            ):
                return m.group(1)

    # Last-resort: find "4.X out of 5" or "4.X stars" anywhere in visible text
    page_text = " ".join(
        t for t in soup.find_all(string=True)
        if t.parent and t.parent.name not in ("script", "style", "noscript")
    )
    m = re.search(r"\b([4-5]\.\d)\s*(?:out of 5|stars?|/\s*5)\b", page_text, re.I)
    if m:
        return m.group(1)
    # Pattern: rating appears just before "(N,NNN ratings)" e.g. "4.8 (4,516 ratings)"
    m = re.search(r"\b([3-5]\.\d)\b[^\d]*\([\d,]+\s+(?:rating|review)", page_text, re.I)
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

    # 2. Active promotion countdown (Apollo/Next payload — has an end timestamp)
    promo = _extract_promo_countdown(soup)
    if promo:
        elements.append(promo)

    # 3. Limited quantity
    qty = _extract_limited_qty(soup)
    if qty:
        elements.append(qty)

    # 4. "Selling fast" or "almost gone"
    fast = _extract_selling_fast(soup)
    if fast:
        elements.append(fast)

    # 5. Expiry / ends date
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


_URGENCY_PHRASE_RE = re.compile(r"selling\s+fast|almost\s+gone|going\s+fast|high\s+demand", re.I)


def _extract_selling_fast(soup: BeautifulSoup) -> Optional[UrgencyElement]:
    el = soup.find(string=_URGENCY_PHRASE_RE)
    if not el:
        return None
    text = el.strip()
    # When the match lands inside a <script> (e.g. the __NEXT_DATA__/Apollo
    # payload), `el` is the entire JSON blob (thousands of chars). Pull just the
    # urgency message text out of it instead of storing the whole blob.
    if len(text) > 200:
        text = _urgency_message_from_blob(text) or ""
    text = text.strip()
    if not text or len(text) > 200:
        return None
    return UrgencyElement(
        element_type="selling_fast",
        element_text=text,
        is_visible_atf=False,
    )


def _urgency_message_from_blob(blob: str) -> Optional[str]:
    """Extract the urgency message (e.g. 'Selling fast!') from an Apollo blob."""
    # Prefer a messageText scoped within an urgencyMessage object.
    m = re.search(
        r'"urgencyMessage"\s*:\s*\{[^{}]*?"messageText"\s*:\s*"([^"]+)"', blob
    )
    if m:
        return m.group(1)
    # Otherwise, any messageText whose value reads like an urgency phrase.
    for mm in re.finditer(r'"messageText"\s*:\s*"([^"]+)"', blob):
        if _URGENCY_PHRASE_RE.search(mm.group(1)):
            return mm.group(1)
    return None


def _extract_promo_countdown(soup: BeautifulSoup) -> Optional[UrgencyElement]:
    """
    Extract an active promotion with an end timestamp from the page's
    Apollo/Next.js payload. Groupon embeds these as:
        "promotion":{...,"message":"Extra $42 off","endsAt":"5/30",
                     "promoEndTimeStamp":"2026-05-30T23:59:00-07:00", ...}
    A live promoEndTimeStamp functions as a countdown timer for urgency.
    """
    payload = _next_data_text(soup)
    if not payload:
        return None
    # Anchor on the end timestamp itself — there can be several promotion
    # objects (one per price tier) and some contain nested objects, so a
    # "promotion":{...} body match is unreliable. The message/endsAt fields
    # precede promoEndTimeStamp within the same object.
    ts_m = re.search(r'"promoEndTimeStamp"\s*:\s*"([^"]+)"', payload)
    if not ts_m or not ts_m.group(1) or ts_m.group(1).lower() == "null":
        return None
    ts = ts_m.group(1)
    window = payload[max(0, ts_m.start() - 400):ts_m.start()]
    msg_matches = re.findall(r'"message"\s*:\s*"([^"]+)"', window)
    ends_matches = re.findall(r'"endsAt"\s*:\s*"([^"]+)"', window)
    message = (msg_matches[-1] if msg_matches else "").strip()
    ends = (ends_matches[-1] if ends_matches else "").strip()
    parts = []
    if message:
        parts.append(message)
    if ends:
        parts.append(f"ends {ends}")
    parts.append(f"promoEndTimeStamp: {ts}")
    return UrgencyElement(
        element_type="countdown",
        element_text=" — ".join(parts),
        is_visible_atf=True,
    )


def _next_data_text(soup: BeautifulSoup) -> Optional[str]:
    """Return the raw text of the __NEXT_DATA__ (or Apollo state) script, if present."""
    script = soup.find("script", id="__NEXT_DATA__")
    if script and script.string:
        return script.string
    # Fallback: any inline script that carries the Apollo/Next payload.
    for s in soup.find_all("script"):
        txt = s.string or ""
        if "promoEndTimeStamp" in txt or "__APOLLO_STATE__" in txt:
            return txt
    return None


def _extract_ends_text(soup: BeautifulSoup) -> Optional[UrgencyElement]:
    pat = re.compile(r"(ends?|expires?|valid\s+through|buy\s+by)\s+(on\s+)?\w+", re.I)
    for el in soup.find_all(string=pat):
        # Skip text inside <script>, <style>, or <noscript> tags
        if el.parent and el.parent.name in ("script", "style", "noscript"):
            continue
        text = el.strip()
        # Ignore JSON-LD blobs (they're long and structured)
        if len(text) > 500 or text.startswith("{") or text.startswith("["):
            continue
        return UrgencyElement(
            element_type="ends_text",
            element_text=text,
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
