"""
SEO parser — meta tags, headings, schema markup, OG tags, page load indicators.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from bs4 import BeautifulSoup

from models import SEOElements


def parse_seo(soup: BeautifulSoup) -> SEOElements:
    """Extract all SEO-relevant elements from a parsed page."""

    # --- Meta tags ---
    meta_title = _meta_content(soup, "name", "title") or _tag_text(soup, "title")
    meta_description = _meta_content(soup, "name", "description")

    # --- Headings ---
    h1 = _tag_text(soup, "h1")
    h2_texts = [
        el.get_text(strip=True)
        for el in soup.find_all("h2")
        if el.get_text(strip=True)
    ]

    # --- Open Graph ---
    og_title = _meta_content(soup, "property", "og:title")
    og_description = _meta_content(soup, "property", "og:description")
    og_image = _meta_content(soup, "property", "og:image")

    # --- Canonical ---
    canonical_tag = soup.find("link", rel=lambda v: v and "canonical" in v)
    canonical_url = canonical_tag.get("href") if canonical_tag else None

    # --- Schema markup (JSON-LD) ---
    schema_type, has_schema = _parse_schema(soup)

    # --- Page load indicators ---
    image_count = len(soup.find_all("img"))
    script_count = len(soup.find_all("script"))

    return SEOElements(
        meta_title=meta_title,
        meta_description=meta_description,
        h1_text=h1,
        h2_texts=h2_texts,
        has_schema_markup=has_schema,
        schema_type=schema_type,
        canonical_url=canonical_url,
        og_title=og_title,
        og_description=og_description,
        og_image_url=og_image,
        image_count=image_count,
        script_count=script_count,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _meta_content(soup: BeautifulSoup, attr: str, value: str) -> Optional[str]:
    """Extract content= from a <meta attr="value"> tag."""
    tag = soup.find("meta", attrs={attr: value})
    if tag:
        content = tag.get("content", "")
        return content.strip() if content else None
    return None


def _tag_text(soup: BeautifulSoup, tag: str) -> Optional[str]:
    el = soup.find(tag)
    if el:
        t = el.get_text(strip=True)
        return t if t else None
    return None


def _parse_schema(soup: BeautifulSoup) -> tuple[Optional[str], bool]:
    """
    Find JSON-LD script tags and extract the @type.
    Returns (schema_type, has_schema_markup).
    """
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            # Can be a single object or a list
            if isinstance(data, list):
                data = data[0]
            if isinstance(data, dict):
                schema_type = data.get("@type")
                return schema_type, True
        except (json.JSONDecodeError, AttributeError):
            continue

    return None, False
