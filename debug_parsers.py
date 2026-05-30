#!/usr/bin/env python3
"""
Parser diagnostic script — run this to see what each parser extracts
from the saved Lucky Strike HTML without running the full pipeline.

Usage:
    python debug_parsers.py [path/to/html_file]

Defaults to: data/raw_html/lucky-strike-bowling-9abde6.html
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

# ── Load HTML ─────────────────────────────────────────────────────────────────
html_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    "data/raw_html/lucky-strike-bowling-9abde6.html"
)
if not html_path.exists():
    print(f"❌ File not found: {html_path}")
    sys.exit(1)

print(f"📄 Loading {html_path} ({html_path.stat().st_size // 1024} KB)…")
html = html_path.read_text(encoding="utf-8")
soup = BeautifulSoup(html, "lxml")
print("✅ Parsed with BeautifulSoup\n")

# ── __NEXT_DATA__ ──────────────────────────────────────────────────────────────
print("=" * 60)
print("__NEXT_DATA__ ANALYSIS")
print("=" * 60)

script = soup.find("script", id="__NEXT_DATA__")
if not script or not script.string:
    print("❌ __NEXT_DATA__ NOT FOUND")
else:
    try:
        nd = json.loads(script.string)
        print(f"✅ Parsed — top-level keys: {list(nd.keys())[:10]}")

        # Find all keys that mention pricing
        def find_price_keys(obj, path="", depth=0):
            if depth > 15:
                return
            if isinstance(obj, dict):
                for k, v in obj.items():
                    full = f"{path}.{k}"
                    kl = k.lower()
                    if any(x in kl for x in ("price", "discount", "option", "tier", "value", "offer")):
                        val_preview = str(v)[:120] if not isinstance(v, (dict, list)) else f"[{type(v).__name__}]"
                        print(f"  {full} = {val_preview}")
                    find_price_keys(v, full, depth + 1)
            elif isinstance(obj, list):
                for i, item in enumerate(obj[:3]):
                    find_price_keys(item, f"{path}[{i}]", depth + 1)

        print("\n--- Keys containing price/discount/option/tier/value/offer ---")
        find_price_keys(nd)

        # Also look for merchant-related keys
        print("\n--- Keys containing merchant/name/brand/provider ---")
        def find_merchant_keys(obj, path="", depth=0):
            if depth > 10:
                return
            if isinstance(obj, dict):
                for k, v in obj.items():
                    full = f"{path}.{k}"
                    kl = k.lower()
                    if any(x in kl for x in ("merchant", "brand", "provider", "seller", "business", "soldby")):
                        val_preview = str(v)[:120] if not isinstance(v, (dict, list)) else f"[{type(v).__name__}]"
                        print(f"  {full} = {val_preview}")
                    find_merchant_keys(v, full, depth + 1)
            elif isinstance(obj, list):
                for i, item in enumerate(obj[:2]):
                    find_merchant_keys(item, f"{path}[{i}]", depth + 1)

        find_merchant_keys(nd)

        # Rating keys
        print("\n--- Keys containing rating/star/review ---")
        def find_rating_keys(obj, path="", depth=0):
            if depth > 10:
                return
            if isinstance(obj, dict):
                for k, v in obj.items():
                    full = f"{path}.{k}"
                    kl = k.lower()
                    if any(x in kl for x in ("rating", "star", "score", "review")):
                        val_preview = str(v)[:120] if not isinstance(v, (dict, list)) else f"[{type(v).__name__}]"
                        print(f"  {full} = {val_preview}")
                    find_rating_keys(v, full, depth + 1)
            elif isinstance(obj, list):
                for i, item in enumerate(obj[:2]):
                    find_rating_keys(item, f"{path}[{i}]", depth + 1)

        find_rating_keys(nd)

    except Exception as e:
        print(f"❌ Failed to parse __NEXT_DATA__: {e}")

# ── JSON-LD schemas ────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("JSON-LD SCHEMAS")
print("=" * 60)

for i, script in enumerate(soup.find_all("script", type="application/ld+json")):
    try:
        data = json.loads(script.string or "")
        schema_type = data.get("@type") if isinstance(data, dict) else (
            [d.get("@type") for d in data if isinstance(d, dict)] if isinstance(data, list) else "?"
        )
        print(f"  Schema {i + 1}: @type={schema_type}")
        if isinstance(data, dict) and data.get("@type") == "FAQPage":
            questions = [e.get("name") for e in data.get("mainEntity", []) if isinstance(e, dict)]
            print(f"    FAQs: {questions[:3]}...")
    except Exception:
        pass

# ── Parser outputs ─────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("PARSER OUTPUTS (updated parsers)")
print("=" * 60)

try:
    from scraper.parsers.deal_parser import (
        parse_title, parse_merchant_name, parse_category,
        parse_city_state, parse_description, parse_highlights,
        parse_fine_print, parse_faqs,
    )
    from scraper.parsers.jsonld_parser import (
        extract_merchant_from_jsonld, extract_rating_from_jsonld,
        extract_review_count_from_jsonld, extract_review_samples_from_jsonld,
        extract_description_from_jsonld, extract_highlights_from_jsonld,
        extract_fine_print_from_jsonld, extract_pricing_from_jsonld,
        find_product_group,
    )
    from scraper.parsers.pricing_parser import parse_pricing_options
    from scraper.parsers.trust_parser import parse_trust_signals, parse_urgency_elements, parse_reviews

    url = "https://www.groupon.com/deals/lucky-strike-bowling"

    print("\n--- JSON-LD Parser ---")
    pg = find_product_group(soup)
    print(f"ProductGroup found: {pg is not None}")
    if pg:
        print(f"  @type: {pg.get('@type')}")
        print(f"  brand: {pg.get('brand')}")
        print(f"  aggregateRating: {pg.get('aggregateRating')}")
        variants = pg.get('hasVariant', [])
        print(f"  hasVariant count: {len(variants)}")
        reviews_ld = pg.get('reviews', [])
        print(f"  reviews count: {len(reviews_ld)}")

    print(f"\nMerchant (JSON-LD):  {extract_merchant_from_jsonld(soup)}")
    print(f"Rating (JSON-LD):    {extract_rating_from_jsonld(soup)}")
    print(f"Reviews# (JSON-LD):  {extract_review_count_from_jsonld(soup)}")

    pricing_ld = extract_pricing_from_jsonld(soup)
    print(f"\nPricing from JSON-LD ({len(pricing_ld)}):")
    for p in pricing_ld:
        print(f"  {p.option_name}: ${p.deal_price} (orig ${p.original_price}, {p.discount_pct}% off)")

    hl_ld = extract_highlights_from_jsonld(soup)
    print(f"\nHighlights from JSON-LD ({len(hl_ld)}): {hl_ld}")

    fp_ld = extract_fine_print_from_jsonld(soup)
    print(f"\nFine print from JSON-LD ({len(fp_ld)}): {fp_ld[:3]}")

    desc_ld = extract_description_from_jsonld(soup)
    print(f"\nDesc from JSON-LD: {(desc_ld[:150] + '...') if desc_ld else None}")

    samples_ld = extract_review_samples_from_jsonld(soup)
    print(f"\nReview samples from JSON-LD ({len(samples_ld)}):")
    for s in samples_ld[:2]:
        print(f"  [{s.rating}★] {s.author}: {s.text[:60]}...")

    print("\n--- HTML Parsers ---")
    print(f"\nTitle:     {parse_title(soup)}")
    print(f"Merchant:  {parse_merchant_name(soup)}")
    print(f"Category:  {parse_category(soup)}")
    print(f"City/State:{parse_city_state(soup, url)}")

    faqs = parse_faqs(soup)
    print(f"FAQs ({len(faqs)}): {[f.question[:50] for f in faqs[:3]]}")

    count, avg, samples = parse_reviews(soup)
    print(f"\nReviews: count={count}, avg={avg}, samples={len(samples)}")

    urgency = parse_urgency_elements(soup)
    print(f"\nUrgency elements ({len(urgency)}):")
    for u in urgency:
        preview = u.element_text[:100] if u.element_text else ""
        print(f"  [{u.element_type}] {preview}")

except ImportError as e:
    print(f"❌ Import error (run from project root): {e}")
except Exception as e:
    import traceback
    traceback.print_exc()
