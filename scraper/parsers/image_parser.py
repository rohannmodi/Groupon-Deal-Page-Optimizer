"""
Image parser — inventory, alt text quality, estimated resolution, type classification.
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from models import DealImage


# Keywords used to classify image type from src URL or alt text
_LIFESTYLE_KEYWORDS = re.compile(
    r"lifestyle|ambiance|interior|exterior|atmosphere|setting|people|person|customer", re.I
)
_PRODUCT_KEYWORDS = re.compile(
    r"product|item|service|treatment|massage|haircut|menu|dish|spa|salon", re.I
)
_LOGO_KEYWORDS = re.compile(r"logo|brand|icon|favicon", re.I)
_STOCK_DOMAINS = {"shutterstock", "istockphoto", "gettyimages", "depositphotos", "dreamstime"}


def parse_images(soup: BeautifulSoup) -> list[DealImage]:
    """Return DealImage objects for all meaningful images on the page."""
    seen_srcs: set[str] = set()
    images: list[DealImage] = []

    # Prioritize carousel / gallery images first
    gallery_imgs = _find_gallery_images(soup)
    all_imgs = gallery_imgs + [
        img for img in soup.find_all("img")
        if img not in gallery_imgs
    ]

    for position, img_tag in enumerate(all_imgs):
        src = _resolve_src(img_tag)
        if not src or src in seen_srcs:
            continue
        if _is_decorative(src, img_tag):
            continue

        seen_srcs.add(src)
        alt = (img_tag.get("alt") or "").strip() or None
        width = _estimated_width(img_tag)
        img_type = _classify_image(src, alt)

        images.append(
            DealImage(
                src_url=src,
                alt_text=alt,
                estimated_width=width,
                image_type=img_type,
                position=position,
            )
        )

    return images


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_gallery_images(soup: BeautifulSoup):
    """Find images inside known carousel / gallery containers."""
    gallery = soup.select_one(
        "[data-qa='deal-gallery'], [class*='Gallery'], [class*='gallery'], "
        "[class*='carousel'], [class*='Carousel'], [class*='slider'], [class*='Slider']"
    )
    if gallery:
        return gallery.find_all("img")
    return []


def _resolve_src(img_tag) -> Optional[str]:
    """Get the best src, preferring data-src (lazy) over src."""
    for attr in ("data-src", "data-lazy-src", "data-original", "src"):
        val = img_tag.get(attr, "").strip()
        if val and val.startswith("http") and not val.endswith(".svg"):
            return val
    # Try srcset — pick the largest
    srcset = img_tag.get("srcset", "")
    if srcset:
        return _largest_from_srcset(srcset)
    return None


def _largest_from_srcset(srcset: str) -> Optional[str]:
    """Parse a srcset attribute and return the URL with the largest declared width."""
    best_url: Optional[str] = None
    best_w = 0
    for entry in srcset.split(","):
        parts = entry.strip().split()
        if not parts:
            continue
        url = parts[0]
        w = 0
        if len(parts) > 1:
            m = re.match(r"(\d+)w", parts[1])
            if m:
                w = int(m.group(1))
        if w > best_w:
            best_w = w
            best_url = url
    return best_url


def _estimated_width(img_tag) -> Optional[int]:
    """Estimate image width from the width attribute or srcset."""
    w = img_tag.get("width")
    if w:
        try:
            return int(str(w).replace("px", "").strip())
        except ValueError:
            pass
    # Try srcset to infer max width
    srcset = img_tag.get("srcset", "")
    if srcset:
        widths = [
            int(m.group(1))
            for entry in srcset.split(",")
            for m in [re.search(r"(\d+)w", entry)]
            if m
        ]
        if widths:
            return max(widths)
    return None


def _is_decorative(src: str, img_tag) -> bool:
    """Return True for images that are tracking pixels, icons, or tiny spacers."""
    # 1x1 tracking pixels
    w = img_tag.get("width", "")
    h = img_tag.get("height", "")
    if str(w) in ("0", "1") or str(h) in ("0", "1"):
        return True
    # SVG data URIs
    if src.startswith("data:image/svg"):
        return True
    # Tiny images from the URL path
    if re.search(r"1x1|spacer|blank|pixel|tracker", src, re.I):
        return True
    return False


def _classify_image(src: str, alt: Optional[str]) -> str:
    """Classify an image as product, lifestyle, stock, logo, or unknown."""
    combined = f"{src} {alt or ''}".lower()

    if _LOGO_KEYWORDS.search(combined):
        return "logo"

    domain = urlparse(src).netloc.lower()
    if any(stock in domain for stock in _STOCK_DOMAINS):
        return "stock"

    if _PRODUCT_KEYWORDS.search(combined):
        return "product"

    if _LIFESTYLE_KEYWORDS.search(combined):
        return "lifestyle"

    return "unknown"
