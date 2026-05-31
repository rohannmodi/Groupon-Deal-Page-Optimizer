"""
Tests for scraper/parsers/image_parser.py

Covers the pure functions (no Playwright, no network):
  - _classify_image
  - _largest_from_srcset
  - _is_decorative
  - _resolve_src
  - parse_images (integration — builds a real BeautifulSoup tree)
"""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from scraper.parsers.image_parser import (
    _classify_image,
    _is_decorative,
    _largest_from_srcset,
    _resolve_src,
    parse_images,
)


# ---------------------------------------------------------------------------
# _classify_image
# ---------------------------------------------------------------------------

class TestClassifyImage:
    def test_logo_from_url(self):
        assert _classify_image("https://cdn.example.com/logo.png", None) == "logo"

    def test_logo_from_alt(self):
        assert _classify_image("https://cdn.example.com/img.jpg", "Brand logo") == "logo"

    def test_stock_from_domain(self):
        assert _classify_image(
            "https://www.shutterstock.com/image-photo/spa-123.jpg", None
        ) == "stock"

    def test_stock_istockphoto(self):
        assert _classify_image(
            "https://media.istockphoto.com/photos/massage.jpg", "massage"
        ) == "stock"

    def test_product_from_keyword_in_url(self):
        result = _classify_image("https://cdn.example.com/massage-treatment.jpg", None)
        assert result == "product"

    def test_product_from_alt_text(self):
        result = _classify_image("https://cdn.example.com/img123.jpg", "Haircut service photo")
        assert result == "product"

    def test_lifestyle_from_alt(self):
        # "interior" is a lifestyle keyword; no product/logo/stock keywords present
        result = _classify_image("https://cdn.example.com/img.jpg", "Relaxing interior")
        assert result == "lifestyle"

    def test_lifestyle_atmosphere(self):
        result = _classify_image("https://cdn.example.com/atmosphere.jpg", None)
        assert result == "lifestyle"

    def test_unknown_fallback(self):
        result = _classify_image("https://cdn.example.com/abc123.jpg", None)
        assert result == "unknown"

    def test_logo_takes_priority_over_product(self):
        # "logo" keyword should win even if alt mentions product
        result = _classify_image("https://cdn.example.com/logo-spa.jpg", "spa treatment")
        assert result == "logo"


# ---------------------------------------------------------------------------
# _largest_from_srcset
# ---------------------------------------------------------------------------

class TestLargestFromSrcset:
    def test_picks_largest_width(self):
        srcset = "img-400.jpg 400w, img-800.jpg 800w, img-1200.jpg 1200w"
        assert _largest_from_srcset(srcset) == "img-1200.jpg"

    def test_single_entry(self):
        assert _largest_from_srcset("img.jpg 600w") == "img.jpg"

    def test_no_width_descriptors(self):
        # Falls back to last URL or None — just shouldn't crash
        result = _largest_from_srcset("img-a.jpg, img-b.jpg")
        assert result is None or isinstance(result, str)

    def test_empty_srcset(self):
        assert _largest_from_srcset("") is None

    def test_with_spaces_around_commas(self):
        srcset = " img-300.jpg 300w , img-600.jpg 600w "
        assert _largest_from_srcset(srcset) == "img-600.jpg"


# ---------------------------------------------------------------------------
# _is_decorative
# ---------------------------------------------------------------------------

class TestIsDecorative:
    def _make_tag(self, src: str, width: str = "", height: str = "") -> BeautifulSoup:
        """Helper to build a minimal <img> tag."""
        attrs = f'src="{src}"'
        if width:
            attrs += f' width="{width}"'
        if height:
            attrs += f' height="{height}"'
        return BeautifulSoup(f"<img {attrs}>", "lxml").find("img")

    def test_1x1_tracking_pixel_width(self):
        tag = self._make_tag("https://track.example.com/pixel.gif", width="1")
        assert _is_decorative("https://track.example.com/pixel.gif", tag) is True

    def test_1x1_tracking_pixel_height(self):
        tag = self._make_tag("https://track.example.com/pixel.gif", height="1")
        assert _is_decorative("https://track.example.com/pixel.gif", tag) is True

    def test_zero_width_decorative(self):
        tag = self._make_tag("https://example.com/spacer.gif", width="0")
        assert _is_decorative("https://example.com/spacer.gif", tag) is True

    def test_svg_data_uri_decorative(self):
        src = "data:image/svg+xml,%3Csvg%3E%3C/svg%3E"
        tag = self._make_tag(src)
        assert _is_decorative(src, tag) is True

    def test_spacer_in_url(self):
        tag = self._make_tag("https://cdn.example.com/spacer.gif")
        assert _is_decorative("https://cdn.example.com/spacer.gif", tag) is True

    def test_normal_image_not_decorative(self):
        tag = self._make_tag("https://cdn.example.com/massage-photo.jpg", width="800")
        assert _is_decorative("https://cdn.example.com/massage-photo.jpg", tag) is False


# ---------------------------------------------------------------------------
# _resolve_src
# ---------------------------------------------------------------------------

class TestResolveSrc:
    def _img(self, html: str):
        return BeautifulSoup(html, "lxml").find("img")

    def test_prefers_data_src_over_src(self):
        tag = self._img('<img src="low.jpg" data-src="https://cdn.example.com/high.jpg">')
        assert _resolve_src(tag) == "https://cdn.example.com/high.jpg"

    def test_falls_back_to_src(self):
        tag = self._img('<img src="https://cdn.example.com/photo.jpg">')
        assert _resolve_src(tag) == "https://cdn.example.com/photo.jpg"

    def test_ignores_svg_sources(self):
        tag = self._img('<img src="https://cdn.example.com/icon.svg">')
        assert _resolve_src(tag) is None

    def test_uses_srcset_when_no_src(self):
        tag = self._img(
            '<img srcset="https://cdn.example.com/img-400.jpg 400w, '
            'https://cdn.example.com/img-800.jpg 800w">'
        )
        result = _resolve_src(tag)
        assert result == "https://cdn.example.com/img-800.jpg"

    def test_returns_none_for_empty_img(self):
        tag = self._img("<img>")
        assert _resolve_src(tag) is None


# ---------------------------------------------------------------------------
# parse_images — integration test with real HTML
# ---------------------------------------------------------------------------

class TestParseImages:
    def test_extracts_images(self):
        html = """
        <html><body>
          <img src="https://cdn.example.com/spa-1.jpg" alt="Relaxing massage" width="800">
          <img src="https://cdn.example.com/spa-2.jpg" alt="Interior view" width="800">
        </body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        images = parse_images(soup)
        assert len(images) == 2

    def test_filters_tracking_pixels(self):
        html = """
        <html><body>
          <img src="https://cdn.example.com/main.jpg" alt="Main" width="800">
          <img src="https://track.com/pixel.gif" width="1" height="1">
        </body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        images = parse_images(soup)
        # Only the main image should survive
        assert len(images) == 1
        assert "main.jpg" in images[0].src_url

    def test_deduplicates_by_url(self):
        html = """
        <html><body>
          <img src="https://cdn.example.com/spa.jpg" alt="Spa">
          <img src="https://cdn.example.com/spa.jpg" alt="Spa again">
        </body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        images = parse_images(soup)
        assert len(images) == 1

    def test_empty_page_returns_empty_list(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert parse_images(soup) == []

    def test_gallery_images_first(self):
        html = """
        <html><body>
          <img src="https://cdn.example.com/standalone.jpg" alt="Standalone" width="400">
          <div data-qa="deal-gallery">
            <img src="https://cdn.example.com/gallery-1.jpg" alt="Gallery 1" width="800">
          </div>
        </body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        images = parse_images(soup)
        # Gallery image should come first (position 0)
        assert images[0].src_url == "https://cdn.example.com/gallery-1.jpg"
