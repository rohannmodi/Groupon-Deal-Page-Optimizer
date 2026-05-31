"""
Tests for scraper/parsers/seo_parser.py

Covers parse_seo and its helpers against real HTML strings.
No network, no browser needed.
"""

from __future__ import annotations

import json

import pytest
from bs4 import BeautifulSoup

from scraper.parsers.seo_parser import parse_seo


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


# ---------------------------------------------------------------------------
# Meta tags
# ---------------------------------------------------------------------------

class TestMetaTags:
    def test_extracts_meta_title(self):
        soup = _soup('<html><head><meta name="title" content="Deal Title | Groupon"></head></html>')
        result = parse_seo(soup)
        assert result.meta_title == "Deal Title | Groupon"

    def test_falls_back_to_title_tag(self):
        soup = _soup("<html><head><title>Fallback Title</title></head></html>")
        result = parse_seo(soup)
        assert result.meta_title == "Fallback Title"

    def test_extracts_meta_description(self):
        soup = _soup(
            '<html><head>'
            '<meta name="description" content="A great spa deal in Chicago.">'
            '</head></html>'
        )
        result = parse_seo(soup)
        assert result.meta_description == "A great spa deal in Chicago."

    def test_missing_meta_title_is_none(self):
        soup = _soup("<html><head></head></html>")
        result = parse_seo(soup)
        assert result.meta_title is None

    def test_missing_meta_description_is_none(self):
        soup = _soup("<html><head></head></html>")
        result = parse_seo(soup)
        assert result.meta_description is None


# ---------------------------------------------------------------------------
# Open Graph
# ---------------------------------------------------------------------------

class TestOpenGraph:
    def test_extracts_og_title(self):
        soup = _soup(
            '<html><head>'
            '<meta property="og:title" content="OG Title">'
            '</head></html>'
        )
        result = parse_seo(soup)
        assert result.og_title == "OG Title"

    def test_extracts_og_description(self):
        soup = _soup(
            '<html><head>'
            '<meta property="og:description" content="OG description text">'
            '</head></html>'
        )
        result = parse_seo(soup)
        assert result.og_description == "OG description text"

    def test_extracts_og_image(self):
        soup = _soup(
            '<html><head>'
            '<meta property="og:image" content="https://cdn.example.com/og.jpg">'
            '</head></html>'
        )
        result = parse_seo(soup)
        assert result.og_image_url == "https://cdn.example.com/og.jpg"


# ---------------------------------------------------------------------------
# Headings
# ---------------------------------------------------------------------------

class TestHeadings:
    def test_extracts_h1(self):
        soup = _soup("<html><body><h1>Swedish Massage in Chicago</h1></body></html>")
        result = parse_seo(soup)
        assert result.h1_text == "Swedish Massage in Chicago"

    def test_h1_missing_is_none(self):
        soup = _soup("<html><body><p>No heading here</p></body></html>")
        result = parse_seo(soup)
        assert result.h1_text is None

    def test_extracts_multiple_h2(self):
        soup = _soup(
            "<html><body>"
            "<h2>What You Get</h2>"
            "<h2>Fine Print</h2>"
            "<h2>About the Merchant</h2>"
            "</body></html>"
        )
        result = parse_seo(soup)
        assert len(result.h2_texts) == 3
        assert "What You Get" in result.h2_texts

    def test_empty_h2_tags_excluded(self):
        soup = _soup("<html><body><h2></h2><h2>Real Heading</h2></body></html>")
        result = parse_seo(soup)
        assert result.h2_texts == ["Real Heading"]


# ---------------------------------------------------------------------------
# Schema markup
# ---------------------------------------------------------------------------

class TestSchemaMarkup:
    def test_detects_json_ld(self):
        schema = json.dumps({"@type": "Product", "name": "Swedish Massage"})
        soup = _soup(
            f'<html><head>'
            f'<script type="application/ld+json">{schema}</script>'
            f'</head></html>'
        )
        result = parse_seo(soup)
        assert result.has_schema_markup is True
        assert result.schema_type == "Product"

    def test_no_schema_markup(self):
        soup = _soup("<html><head></head></html>")
        result = parse_seo(soup)
        assert result.has_schema_markup is False
        assert result.schema_type is None

    def test_schema_list_uses_first_item(self):
        schema = json.dumps([
            {"@type": "WebSite"},
            {"@type": "Product"},
        ])
        soup = _soup(
            f'<html><head>'
            f'<script type="application/ld+json">{schema}</script>'
            f'</head></html>'
        )
        result = parse_seo(soup)
        assert result.schema_type == "WebSite"

    def test_malformed_json_ld_skipped(self):
        soup = _soup(
            '<html><head>'
            '<script type="application/ld+json">{broken json}</script>'
            '</head></html>'
        )
        result = parse_seo(soup)
        assert result.has_schema_markup is False


# ---------------------------------------------------------------------------
# Canonical URL
# ---------------------------------------------------------------------------

class TestCanonicalUrl:
    def test_extracts_canonical(self):
        soup = _soup(
            '<html><head>'
            '<link rel="canonical" href="https://www.groupon.com/deals/spa-deal">'
            '</head></html>'
        )
        result = parse_seo(soup)
        assert result.canonical_url == "https://www.groupon.com/deals/spa-deal"

    def test_missing_canonical_is_none(self):
        soup = _soup("<html><head></head></html>")
        result = parse_seo(soup)
        assert result.canonical_url is None


# ---------------------------------------------------------------------------
# Page load indicators
# ---------------------------------------------------------------------------

class TestPageLoadIndicators:
    def test_counts_images(self):
        soup = _soup(
            "<html><body>"
            '<img src="a.jpg"><img src="b.jpg"><img src="c.jpg">'
            "</body></html>"
        )
        result = parse_seo(soup)
        assert result.image_count == 3

    def test_counts_scripts(self):
        soup = _soup(
            "<html><head>"
            "<script>x</script><script>y</script>"
            "</head></html>"
        )
        result = parse_seo(soup)
        assert result.script_count == 2

    def test_empty_page_zero_counts(self):
        soup = _soup("<html><body></body></html>")
        result = parse_seo(soup)
        assert result.image_count == 0
        assert result.script_count == 0
