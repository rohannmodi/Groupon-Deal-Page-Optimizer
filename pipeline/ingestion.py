"""
URL ingestion — reads deals.txt (or a list of URLs) and produces stable DealInput objects.

deal_id generation rules:
  - Derived from the URL, never random
  - Stable across re-runs (same URL → same ID)
  - Human-readable: "{merchant_slug}-{city_slug}-{short_hash}"
  - Safe for filenames and DuckDB keys
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from models import DealInput


def load_deals(source: str | Path | list[str]) -> list[DealInput]:
    """
    Load deals from:
      - A file path (str or Path) → reads one URL per non-blank, non-comment line
      - A list of URL strings

    Returns a deduplicated list of DealInput objects, preserving order.
    """
    if isinstance(source, (str, Path)):
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"Deals file not found: {path}")
        raw_urls = [
            line.strip()
            for line in path.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    else:
        raw_urls = [u.strip() for u in source if u.strip()]

    seen: set[str] = set()
    deals: list[DealInput] = []
    for url in raw_urls:
        url = _normalise_url(url)
        if url in seen:
            continue
        seen.add(url)
        deals.append(DealInput(deal_id=_make_deal_id(url), url=url))

    return deals


def _normalise_url(url: str) -> str:
    """Strip tracking params and trailing slashes for stable IDs."""
    # Remove common tracking query params
    url = re.sub(r"\?(utm_[^&]*&?|fbclid=[^&]*&?|gclid=[^&]*&?)+", "", url)
    url = re.sub(r"[?&]$", "", url)
    return url.rstrip("/")


def _make_deal_id(url: str) -> str:
    """
    Create a stable, human-readable deal ID from a URL.

    Example:
      https://www.groupon.com/deals/ga-great-clips-6
      → "great-clips-ga-a3f2b1"
    """
    # Extract path segments: /deals/{city-or-state}/{slug}
    m = re.search(r"/deals?/([^/?#]+)(?:/([^/?#]+))?", url)
    if m:
        part1 = _slugify(m.group(1) or "")
        part2 = _slugify(m.group(2) or "")
        label = f"{part2}-{part1}" if part2 else part1
    else:
        label = _slugify(url.split("/")[-1] or "deal")

    # Append short hash for uniqueness (in case slugs collide)
    short_hash = hashlib.md5(url.encode()).hexdigest()[:6]
    deal_id = f"{label[:40]}-{short_hash}".strip("-")

    # Make filesystem-safe
    deal_id = re.sub(r"[^a-z0-9\-]", "", deal_id)
    deal_id = re.sub(r"-+", "-", deal_id).strip("-")
    return deal_id or short_hash


def _slugify(text: str) -> str:
    """Convert arbitrary text to a lowercase hyphenated slug."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")
