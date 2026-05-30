"""
Next.js data extractor for Groupon pages.

Groupon is a Next.js app. Every page embeds all its structured data in a
<script id="__NEXT_DATA__"> tag as a JSON blob. This is far more reliable
than scraping rendered CSS classes (which change frequently) or guessing
selector names.

The JSON tree depth varies by page type. We use a recursive key search to
find the deal payload regardless of nesting level.

Public API
----------
extract_next_data(soup)           → raw dict or {}
find_key_recursive(obj, key, ...)  → first value matching key name
extract_pricing_from_next_data(d)  → list[PricingOption]
extract_merchant_from_next_data(d) → str | None
extract_rating_from_next_data(d)   → float | None
extract_description_from_next_data(d) → str | None
extract_highlights_from_next_data(d)  → list[str]
extract_fine_print_from_next_data(d)  → list[str]
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from bs4 import BeautifulSoup

from models import PricingOption

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Core extractor
# ---------------------------------------------------------------------------

def extract_next_data(soup: BeautifulSoup) -> dict:
    """Return the parsed __NEXT_DATA__ JSON dict, or {} on failure."""
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return {}
    try:
        return json.loads(script.string)
    except Exception as exc:
        log.debug("Failed to parse __NEXT_DATA__: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Generic recursive key finder
# ---------------------------------------------------------------------------

def find_key_recursive(
    obj: Any,
    key: str,
    *,
    max_depth: int = 20,
    _depth: int = 0,
) -> Any:
    """
    Recursively search a nested dict/list for the FIRST occurrence of `key`.
    Returns the value, or None if not found within max_depth.
    """
    if _depth > max_depth:
        return None
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            result = find_key_recursive(v, key, max_depth=max_depth, _depth=_depth + 1)
            if result is not None:
                return result
    elif isinstance(obj, list):
        for item in obj:
            result = find_key_recursive(item, key, max_depth=max_depth, _depth=_depth + 1)
            if result is not None:
                return result
    return None


def find_all_key_recursive(
    obj: Any,
    key: str,
    *,
    max_depth: int = 20,
    _depth: int = 0,
) -> list:
    """Return ALL values for a given key at any depth."""
    results = []
    if _depth > max_depth:
        return results
    if isinstance(obj, dict):
        if key in obj:
            results.append(obj[key])
        for v in obj.values():
            results.extend(find_all_key_recursive(v, key, max_depth=max_depth, _depth=_depth + 1))
    elif isinstance(obj, list):
        for item in obj:
            results.extend(find_all_key_recursive(item, key, max_depth=max_depth, _depth=_depth + 1))
    return results


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------

_PRICE_RE = re.compile(r"[\$]?\s*([\d,]+(?:\.\d{1,2})?)")


def _parse_money(val: Any) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).replace(",", "").replace("$", "").strip()
    m = _PRICE_RE.search(s)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def extract_pricing_from_next_data(data: dict) -> list[PricingOption]:
    """
    Extract pricing options from the __NEXT_DATA__ blob.

    Groupon stores options in structures like:
      props.pageProps.deal.options / dealOptions / tiers / pricingTiers
    Each option typically has fields like:
      price / dealPrice / discountedPrice / salePrice → deal price
      value / originalPrice / listPrice / regularPrice → original price
      discount / discountPct / percentOff → discount %
      title / name / label → option name
    """
    if not data:
        return []

    options: list[PricingOption] = []

    # Try common container key names
    option_lists: list[list] = []
    for key in ("options", "dealOptions", "tiers", "pricingTiers", "purchaseOptions", "optionList"):
        found = find_all_key_recursive(data, key)
        for item in found:
            if isinstance(item, list) and len(item) > 0:
                option_lists.append(item)

    # De-duplicate (same list might be referenced multiple times)
    seen_ids: set[int] = set()
    unique_lists: list[list] = []
    for lst in option_lists:
        lid = id(lst)
        if lid not in seen_ids:
            seen_ids.add(lid)
            unique_lists.append(lst)

    # Pick the list that looks most like pricing options
    best_list: list = []
    best_score = 0
    for lst in unique_lists:
        score = 0
        for item in lst[:3]:
            if not isinstance(item, dict):
                continue
            keys_lower = {k.lower() for k in item.keys()}
            if any(k in keys_lower for k in ("price", "dealprice", "value", "discount")):
                score += 2
            if any(k in keys_lower for k in ("title", "name", "label")):
                score += 1
        if score > best_score:
            best_score = score
            best_list = lst

    if not best_list:
        return []

    for i, item in enumerate(best_list):
        if not isinstance(item, dict):
            continue
        item_keys_lower = {k.lower(): k for k in item.keys()}

        def get(key_lower: str) -> Any:
            real_key = item_keys_lower.get(key_lower)
            return item.get(real_key) if real_key else None

        # Deal price — try multiple key names
        deal_price = None
        for k in ("price", "dealprice", "saleprice", "discountedprice", "dealamount"):
            deal_price = _parse_money(get(k))
            if deal_price:
                break

        # Original price
        orig_price = None
        for k in ("value", "originalprice", "listprice", "regularprice", "retailprice", "msrp"):
            orig_price = _parse_money(get(k))
            if orig_price:
                break

        # Discount %
        disc = None
        for k in ("discount", "discountpct", "percentoff", "savingspercent", "discountpercent"):
            v = get(k)
            if v is not None:
                try:
                    disc = float(v)
                    if disc > 1:  # percentage, not fraction
                        pass
                    else:
                        disc = disc * 100
                    break
                except (TypeError, ValueError):
                    pass

        if deal_price is None and orig_price is None:
            continue  # not a pricing option

        if disc is None and orig_price and deal_price and orig_price > deal_price:
            disc = round((orig_price - deal_price) / orig_price * 100, 1)

        savings = round(orig_price - deal_price, 2) if orig_price and deal_price else None

        # Name
        name = None
        for k in ("title", "name", "label", "description", "optionname"):
            name = get(k)
            if isinstance(name, str) and name.strip():
                name = name.strip()
                break

        options.append(
            PricingOption(
                option_name=name or f"Option {i + 1}",
                original_price=orig_price,
                deal_price=deal_price,
                discount_pct=disc,
                savings_amount=savings,
                is_primary=(i == 0),
            )
        )

    return options


# ---------------------------------------------------------------------------
# Merchant name
# ---------------------------------------------------------------------------

_EXCLUDED_MERCHANTS = {
    "sell on groupon", "groupon", "see all deals", "shop", "deals",
    "local", "things to do", "all deals", "home",
}


def extract_merchant_from_next_data(data: dict) -> Optional[str]:
    """Try multiple key names for the merchant / business name."""
    for key in ("merchantName", "merchant", "businessName", "providerName",
                "sellerName", "soldBy", "brand", "name"):
        val = find_key_recursive(data, key)
        if isinstance(val, str) and val.strip():
            v = val.strip()
            if v.lower() not in _EXCLUDED_MERCHANTS:
                return v
    return None


# ---------------------------------------------------------------------------
# Rating
# ---------------------------------------------------------------------------

def extract_rating_from_next_data(data: dict) -> Optional[float]:
    for key in ("averageRating", "avgRating", "rating", "starRating", "ratingValue",
                "averageScore", "score"):
        val = find_key_recursive(data, key)
        if val is not None:
            try:
                f = float(val)
                if 0 < f <= 5:
                    return f
            except (TypeError, ValueError):
                pass
    return None


# ---------------------------------------------------------------------------
# Description / highlights / fine print
# ---------------------------------------------------------------------------

def extract_description_from_next_data(data: dict) -> Optional[str]:
    for key in ("description", "longDescription", "body", "dealDescription", "about"):
        val = find_key_recursive(data, key)
        if isinstance(val, str) and len(val) > 40:
            return val.strip()
    return None


def extract_highlights_from_next_data(data: dict) -> list[str]:
    for key in ("highlights", "whatYouGet", "inclusions", "included", "features", "bullets"):
        val = find_key_recursive(data, key)
        if isinstance(val, list) and val:
            return [str(h).strip() for h in val if str(h).strip()]
        if isinstance(val, str) and val.strip():
            return [line.strip() for line in val.split("\n") if line.strip()]
    return []


def extract_fine_print_from_next_data(data: dict) -> list[str]:
    for key in ("finePrint", "fineprint", "terms", "restrictions", "termsAndConditions",
                "conditions", "legalDisclaimer"):
        val = find_key_recursive(data, key)
        if isinstance(val, list) and val:
            return [str(h).strip() for h in val if str(h).strip()]
        if isinstance(val, str) and val.strip():
            return [line.strip() for line in val.split("\n") if line.strip()]
    return []
