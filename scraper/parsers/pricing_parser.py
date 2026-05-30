"""
Pricing parser — extracts all pricing tiers from a Groupon deal page.

Groupon pages can have:
  - A single price (simple deal)
  - Multiple options (e.g., "1 Session / 3 Sessions / 5 Sessions")
  - A primary "featured" option + secondary options

We try to capture all tiers and compute discount / savings where missing.
"""

from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup, Tag

from models import PricingOption
from scraper.parsers.next_data_parser import extract_next_data, extract_pricing_from_next_data


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_pricing_options(soup: BeautifulSoup) -> list[PricingOption]:
    """
    Return a list of all pricing options found on the page.

    NOTE: groupon.py now calls extract_pricing_from_jsonld() before
    this function, so this is effectively a fallback for pages that
    don't have a ProductGroup JSON-LD schema.
    """

    # Strategy 0: __NEXT_DATA__ JSON
    next_data = extract_next_data(soup)
    if next_data:
        options = extract_pricing_from_next_data(next_data)
        if options:
            return options

    options: list[PricingOption] = []

    # Strategy 1: structured option cards (multi-option deals)
    option_cards = _find_option_cards(soup)
    if option_cards:
        for i, card in enumerate(option_cards):
            opt = _parse_option_card(card, is_primary=(i == 0))
            if opt:
                options.append(opt)
        return options

    # Strategy 2: single-price layout
    single = _parse_single_price(soup)
    if single:
        return [single]

    return options


# ---------------------------------------------------------------------------
# Strategy 1 — multi-option cards
# ---------------------------------------------------------------------------

_OPTION_CARD_SELECTORS = [
    "[data-qa='option-tile']",
    "[data-qa='deal-option']",
    "[class*='OptionTile']",
    "[class*='option-tile']",
    "[class*='DealOption']",
    ".option-card",
    ".tier-card",
]


def _find_option_cards(soup: BeautifulSoup) -> list[Tag]:
    for selector in _OPTION_CARD_SELECTORS:
        cards = soup.select(selector)
        if len(cards) >= 1:
            return cards
    return []


def _parse_option_card(card: Tag, is_primary: bool = False) -> Optional[PricingOption]:
    name = _text(
        card.find(attrs={"data-qa": "option-title"})
        or card.select_one("[class*='OptionTitle'], [class*='option-title'], h3, h4")
    )

    deal_price = _extract_price(
        card.find(attrs={"data-qa": "option-price"})
        or card.find(attrs={"data-qa": "deal-price"})
        or card.select_one("[class*='DealPrice'], [class*='deal-price'], .price-value")
    )

    original_price = _extract_price(
        card.find(attrs={"data-qa": "value-price"})
        or card.find(attrs={"data-qa": "original-price"})
        or card.select_one(
            "[class*='ValuePrice'], [class*='original-price'], s, del, strike, .was-price"
        )
    )

    discount_pct = _extract_discount_pct(card)
    savings = _compute_savings(original_price, deal_price)
    if discount_pct is None and original_price and deal_price:
        discount_pct = round((original_price - deal_price) / original_price * 100, 1)

    return PricingOption(
        option_name=name or "Standard",
        original_price=original_price,
        deal_price=deal_price,
        discount_pct=discount_pct,
        savings_amount=savings,
        is_primary=is_primary,
    )


# ---------------------------------------------------------------------------
# Strategy 2 — single price layout
# ---------------------------------------------------------------------------

def _parse_single_price(soup: BeautifulSoup) -> Optional[PricingOption]:
    # Deal price
    deal_price = _extract_price(
        soup.find(attrs={"data-qa": "deal-price"})
        or soup.select_one(
            "[class*='deal-price'], [class*='DealPrice'], "
            ".price-amount, .current-price, [itemprop='price']"
        )
    )

    # Original / value price (usually in a <s> or <del> tag)
    original_price = _extract_price(
        soup.find(attrs={"data-qa": "value-price"})
        or soup.select_one(
            "s[class*='price'], del[class*='price'], .was-price, "
            ".original-price, [class*='ValuePrice'], [itemprop='originalPrice']"
        )
        or soup.find("s")  # last resort: first strikethrough element
        or soup.find("del")
    )

    if deal_price is None:
        return None

    discount_pct = _extract_discount_pct(soup)
    savings = _compute_savings(original_price, deal_price)
    if discount_pct is None and original_price and deal_price:
        discount_pct = round((original_price - deal_price) / original_price * 100, 1)

    return PricingOption(
        option_name="Standard",
        original_price=original_price,
        deal_price=deal_price,
        discount_pct=discount_pct,
        savings_amount=savings,
        is_primary=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PRICE_RE = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")


def _extract_price(el: Tag | None) -> Optional[float]:
    if el is None:
        return None
    text = el.get_text(strip=True)
    m = _PRICE_RE.search(text)
    if m:
        return float(m.group(1).replace(",", ""))
    # Check data attributes (Groupon sometimes puts price in data-price)
    for attr in ("data-price", "data-amount", "content"):
        val = el.get(attr, "")
        if val:
            try:
                return float(str(val).replace(",", "").replace("$", "").strip())
            except ValueError:
                pass
    return None


def _extract_discount_pct(el: Tag) -> Optional[float]:
    """Look for an explicit discount % label like '75% Off'."""
    text = el.get_text()
    m = re.search(r"(\d+)\s*%\s*[Oo]ff", text)
    if m:
        return float(m.group(1))
    return None


def _compute_savings(original: Optional[float], deal: Optional[float]) -> Optional[float]:
    if original is not None and deal is not None:
        return round(original - deal, 2)
    return None


def _text(el: Tag | None) -> Optional[str]:
    if el is None:
        return None
    t = el.get_text(strip=True)
    return t if t else None
