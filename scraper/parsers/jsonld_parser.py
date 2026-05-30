"""
JSON-LD parser for Groupon deal pages.

Groupon embeds a rich `ProductGroup` schema in the page as:
    <script type="application/ld+json">[{"@type":"ProductGroup", ...}]</script>

This is far more reliable than CSS selectors because Groupon's React classes
change frequently but the structured data schema stays stable.

Extraction targets
------------------
  ProductGroup.brand.name          → merchant name
  ProductGroup.aggregateRating     → rating + review count
  ProductGroup.description         → description (raw rich text)
  ProductGroup.hasVariant[]        → pricing options (via offers.price)
  ProductGroup.reviews[]           → sample reviews

We also parse the `description` text to extract:
  - highlights   (everything after "What We Offer" up to the next paragraph)
  - fine_print   (restriction/term sentences in the body)

Public API
----------
  find_product_group(soup)                → dict | None
  extract_merchant_from_jsonld(soup)      → str | None
  extract_rating_from_jsonld(soup)        → float | None
  extract_review_count_from_jsonld(soup)  → int | None
  extract_review_samples_from_jsonld(soup) → list[ReviewSample]
  extract_description_from_jsonld(soup)   → str | None
  extract_pricing_from_jsonld(soup)       → list[PricingOption]
  extract_highlights_from_jsonld(soup)    → list[str]
  extract_fine_print_from_jsonld(soup)    → list[str]
"""

from __future__ import annotations

import json
import re
from typing import Optional

from bs4 import BeautifulSoup

from models import PricingOption, ReviewSample


# ---------------------------------------------------------------------------
# Core finder
# ---------------------------------------------------------------------------

def find_product_group(soup: BeautifulSoup) -> Optional[dict]:
    """
    Find and return the ProductGroup (or Product) JSON-LD dict, or None.
    Groupon wraps it in an array: [{"@type":"ProductGroup", ...}]
    """
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            raw = script.string or ""
            data = json.loads(raw)
            items = data if isinstance(data, list) else [data]
            for item in items:
                if isinstance(item, dict) and item.get("@type") in ("ProductGroup", "Product"):
                    return item
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Merchant name
# ---------------------------------------------------------------------------

def extract_merchant_from_jsonld(soup: BeautifulSoup) -> Optional[str]:
    pg = find_product_group(soup)
    if not pg:
        return None
    brand = pg.get("brand") or {}
    if isinstance(brand, dict):
        name = brand.get("name", "")
        if name:
            return name.strip()
    # Fall back to top-level 'name' (but that's the deal title, so skip)
    return None


# ---------------------------------------------------------------------------
# Rating + review count
# ---------------------------------------------------------------------------

def extract_rating_from_jsonld(soup: BeautifulSoup) -> Optional[float]:
    pg = find_product_group(soup)
    if not pg:
        return None
    ar = pg.get("aggregateRating") or {}
    if isinstance(ar, dict):
        val = ar.get("ratingValue")
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    return None


def extract_review_count_from_jsonld(soup: BeautifulSoup) -> Optional[int]:
    pg = find_product_group(soup)
    if not pg:
        return None
    ar = pg.get("aggregateRating") or {}
    if isinstance(ar, dict):
        for key in ("reviewCount", "ratingCount"):
            val = ar.get(key)
            if val is not None:
                try:
                    return int(val)
                except (TypeError, ValueError):
                    pass
    return None


# ---------------------------------------------------------------------------
# Review samples
# ---------------------------------------------------------------------------

def extract_review_samples_from_jsonld(soup: BeautifulSoup) -> list[ReviewSample]:
    pg = find_product_group(soup)
    if not pg:
        return []
    samples: list[ReviewSample] = []
    for review in pg.get("reviews", []):
        if not isinstance(review, dict):
            continue
        author_obj = review.get("author", {})
        author = author_obj.get("name", "") if isinstance(author_obj, dict) else str(author_obj)
        body = review.get("reviewBody", "").strip()
        date = review.get("datePublished", "")
        rating_obj = review.get("reviewRating", {})
        rating_val: Optional[float] = None
        if isinstance(rating_obj, dict):
            try:
                rating_val = float(rating_obj.get("ratingValue", 0) or 0) or None
            except (TypeError, ValueError):
                pass
        if body:
            samples.append(ReviewSample(
                author=author or None,
                rating=rating_val,
                text=body,
                date=date or None,
            ))
    return samples[:5]  # cap at 5


# ---------------------------------------------------------------------------
# Description
# ---------------------------------------------------------------------------

def extract_description_from_jsonld(soup: BeautifulSoup) -> Optional[str]:
    pg = find_product_group(soup)
    if not pg:
        return None
    desc = pg.get("description", "")
    if desc and len(desc) > 40:
        return desc.strip()
    return None


# ---------------------------------------------------------------------------
# Pricing from hasVariant[]
# ---------------------------------------------------------------------------

def extract_pricing_from_jsonld(soup: BeautifulSoup) -> list[PricingOption]:
    """
    Extract pricing from ProductGroup.hasVariant[].offers.

    JSON-LD structure:
        "hasVariant": [
            {
                "@type": "Product",
                "name": "2 Hours Bowling + Free Shoe Rental - For 2",
                "offers": {
                    "@type": "Offer",
                    "price": 40,              ← deal price
                    "priceCurrency": "USD",
                    "priceSpecification": {
                        "price": 38,          ← member/alternative price (ignore)
                        "priceType": "..."
                    }
                }
            }
        ]

    Original retail prices are NOT in the JSON-LD hasVariant — they appear in the
    rendered React UI only. We try to recover them from structured Product JSON-LD
    on the same page (e.g. individual Product schemas that list strikethrough prices).
    """
    pg = find_product_group(soup)
    if not pg:
        return []

    variants = pg.get("hasVariant", [])
    if not variants:
        return []

    # Build a deal-price → original-price lookup from the page's structured data.
    # Groupon occasionally emits separate Product schemas with both regular and sale price.
    orig_price_map = _build_original_price_map(soup)

    options: list[PricingOption] = []
    for i, variant in enumerate(variants):
        if not isinstance(variant, dict):
            continue

        name = variant.get("name", f"Option {i + 1}").strip()

        offers = variant.get("offers", {})
        if not isinstance(offers, dict):
            continue

        # Deal price
        deal_price: Optional[float] = None
        try:
            deal_price = float(offers.get("price", 0) or 0) or None
        except (TypeError, ValueError):
            pass

        if deal_price is None:
            continue

        # Original price: try lookup map, then compute from description text
        original_price = orig_price_map.get(deal_price)

        # Compute discount %
        disc: Optional[float] = None
        savings: Optional[float] = None
        if original_price and original_price > deal_price:
            disc = round((original_price - deal_price) / original_price * 100, 1)
            savings = round(original_price - deal_price, 2)

        options.append(PricingOption(
            option_name=name,
            original_price=original_price,
            deal_price=deal_price,
            discount_pct=disc,
            savings_amount=savings,
            is_primary=(i == 0),
        ))

    return options


_PRICE_PAIR_RE = re.compile(r"\$(\d[\d,]*(?:\.\d{1,2})?)")


def _build_original_price_map(soup: BeautifulSoup) -> dict[float, float]:
    """
    Build a deal_price → original_price mapping from two sources:

    Strategy 1 — JSON-LD fields (retailPrice, regularPrice, etc.)
      Rarely present in Groupon's current JSON-LD structure.

    Strategy 2 — HTML strikethrough elements (<s> and <del>)
      Groupon renders original prices as strikethrough text directly next to the
      deal price in each pricing option card. e.g.:
        <s>$19.49</s>  →  $12.82 (-34%)
      We scan every <s>/<del> element, extract its price as the original, then look
      for a smaller price in the same parent container as the deal price.
    """
    result: dict[float, float] = {}
    _PX = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")

    # Strategy 1: JSON-LD retailPrice / regularPrice etc.
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                for variant in item.get("hasVariant", []):
                    if not isinstance(variant, dict):
                        continue
                    offers = variant.get("offers", {})
                    if not isinstance(offers, dict):
                        continue
                    deal_p = offers.get("price")
                    for key in ("retailPrice", "regularPrice", "msrp", "highPrice", "originalPrice"):
                        orig_p = offers.get(key)
                        if orig_p is not None:
                            try:
                                d = float(deal_p)
                                o = float(orig_p)
                                if o > d:
                                    result[round(d, 2)] = round(o, 2)
                            except (TypeError, ValueError):
                                pass
        except Exception:
            continue

    # Strategy 2: HTML <s>/<del> strikethrough elements (Groupon's rendered UI)
    # Each strikethrough tag's parent container also contains the deal price —
    # we pick the largest price below the strikethrough as the deal price.
    for strike in soup.find_all(["s", "del"]):
        # Skip if inside script / style / noscript
        if any(getattr(p, "name", "") in ("script", "style", "noscript")
               for p in strike.parents):
            continue

        strike_text = strike.get_text(strip=True)
        m = _PX.search(strike_text)
        if not m:
            continue
        try:
            orig = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        if orig < 2 or orig > 10_000:
            continue

        # Search the immediate parent for a lower price (the deal price)
        parent = strike.parent
        if parent is None:
            continue
        parent_text = parent.get_text(strip=True)
        candidates = []
        for price_str in _PX.findall(parent_text):
            try:
                p = float(price_str.replace(",", ""))
                if 0 < p < orig:
                    candidates.append(p)
            except ValueError:
                pass

        if candidates:
            deal = max(candidates)  # highest price still below orig
            key = round(deal, 2)
            if key not in result:  # don't overwrite JSON-LD match if present
                result[key] = round(orig, 2)

    return result


# ---------------------------------------------------------------------------
# Highlights from description text
# ---------------------------------------------------------------------------

_OFFER_SECTION_RE = re.compile(
    r"(?:What We Offer|Highlights|What You(?:'ll)? Get|What's Included)\s+(.*?)(?:\n\n|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_BULLET_SPLIT_RE = re.compile(r"\s{2,}|\n+")

# CSS rule signatures. Merchant descriptions sometimes embed a raw <style> block
# (e.g. "@media (max-width:600px){ .foo { padding:14px !important; } }"). The
# "What We Offer / Highlights" regex can match the word "highlights" inside a CSS
# class name like ".nymlo-highlights", pulling CSS declarations into the bullet
# list. _looks_like_css lets us drop those fragments.
_CSS_SIGNALS = re.compile(
    r"(\{|\}|!important|@media|@keyframes|:\s*\d+px|"
    r"[.#][A-Za-z][\w-]*\s*\{|[a-z-]+\s*:\s*[^;]+;)",
    re.IGNORECASE,
)


def _looks_like_css(text: str) -> bool:
    """Return True if a string looks like CSS rather than human-readable copy."""
    if not text:
        return False
    return bool(_CSS_SIGNALS.search(text))


def extract_highlights_from_jsonld(soup: BeautifulSoup) -> list[str]:
    """
    Parse the 'What We Offer' section from the ProductGroup description.
    Returns a list of bullet strings.
    """
    desc = extract_description_from_jsonld(soup)
    if not desc:
        return []

    m = _OFFER_SECTION_RE.search(desc)
    if m:
        raw = m.group(1).strip()
        items = [s.strip() for s in _BULLET_SPLIT_RE.split(raw) if s.strip()]
        # Highlights are short bullet points (≤ 60 chars, no terminal period mid-item).
        # Long items are the next paragraph bleeding in.
        highlights = []
        for h in items:
            if _looks_like_css(h):
                # The "Highlights" match landed inside an embedded CSS block
                # (e.g. ".nymlo-highlights { ... }"). Skip CSS fragments — they
                # are never real highlights.
                continue
            if len(h) > 60:
                break  # first long item signals end of the bullet section
            if len(h) >= 3:
                highlights.append(h)
        return highlights

    return []


# ---------------------------------------------------------------------------
# Fine print from description text
# ---------------------------------------------------------------------------

_FINE_PRINT_KEYWORDS = re.compile(
    r"^(not valid|includes?|each voucher|note:|paid value|please check|retail"
    r"|valid at|valid for|limit|may not|cannot|cannot be|per person|per group|black"
    r"|discount|expires?|promo|promotional value|tip not|gratuity|reserv|purchase two"
    r"|advance reservation|advance booking|check .+? prior|hours of operation|call ahead)",
    re.IGNORECASE,
)

# Curriculum / marketing bullets in merchant descriptions are typically written
# as "Topic — descriptive sentence with a learning verb". Real terms never look
# like this, so we exclude them from the fine-print fallback.
_CURRICULUM_RE = re.compile(
    r"\s[—–-]\s.*\b(learn|master|gain|navigate|build|cover|explore|understand|study|"
    r"develop|practice|prepare)\b",
    re.IGNORECASE,
)


def extract_fine_print_from_jsonld(soup: BeautifulSoup) -> list[str]:
    """
    Extract restriction/terms sentences from the ProductGroup description.
    Targets sentences that look like fine print conditions.

    This is a *fallback* only (see scraper/groupon.py): the description is
    merchant marketing copy, so we explicitly reject CSS fragments and
    curriculum/marketing bullets that are not terms & conditions.
    """
    desc = extract_description_from_jsonld(soup)
    if not desc:
        return []

    # Split on sentence boundaries and filter for fine-print-looking sentences
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", desc)
    fine_print: list[str] = []
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if _looks_like_css(s) or _CURRICULUM_RE.search(s):
            continue
        if _FINE_PRINT_KEYWORDS.match(s):
            fine_print.append(s)

    return fine_print
