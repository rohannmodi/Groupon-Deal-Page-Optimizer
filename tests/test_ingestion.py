"""
Tests for pipeline/ingestion.py

Covers:
  - URL normalisation (tracking params stripped)
  - deal_id generation (stable, human-readable, filesystem-safe)
  - load_deals from a list of URLs (dedup, comment skipping)
  - load_deals from a file path
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from pipeline.ingestion import _make_deal_id, _normalise_url, load_deals


# ---------------------------------------------------------------------------
# _normalise_url
# ---------------------------------------------------------------------------

class TestNormaliseUrl:
    def test_strips_trailing_slash(self):
        assert _normalise_url("https://www.groupon.com/deals/spa-deal/") == \
               "https://www.groupon.com/deals/spa-deal"

    def test_strips_utm_params(self):
        url = "https://www.groupon.com/deals/spa-deal?utm_source=email&utm_medium=cpc"
        assert _normalise_url(url) == "https://www.groupon.com/deals/spa-deal"

    def test_strips_fbclid(self):
        url = "https://www.groupon.com/deals/spa-deal?fbclid=abc123"
        assert _normalise_url(url) == "https://www.groupon.com/deals/spa-deal"

    def test_strips_gclid(self):
        url = "https://www.groupon.com/deals/spa-deal?gclid=xyz"
        assert _normalise_url(url) == "https://www.groupon.com/deals/spa-deal"

    def test_preserves_clean_url(self):
        url = "https://www.groupon.com/deals/escape-room-madness-nyc"
        assert _normalise_url(url) == url

    def test_no_trailing_slash_after_strip(self):
        result = _normalise_url("https://www.groupon.com/deals/test/?utm_source=x")
        assert not result.endswith("/")


# ---------------------------------------------------------------------------
# _make_deal_id
# ---------------------------------------------------------------------------

class TestMakeDealId:
    def test_returns_string(self):
        deal_id = _make_deal_id("https://www.groupon.com/deals/spa-deal")
        assert isinstance(deal_id, str)

    def test_stable_same_url(self):
        url = "https://www.groupon.com/deals/nomad-organic-foot-spa-2"
        assert _make_deal_id(url) == _make_deal_id(url)

    def test_different_urls_different_ids(self):
        a = _make_deal_id("https://www.groupon.com/deals/spa-deal-chicago")
        b = _make_deal_id("https://www.groupon.com/deals/massage-deal-nyc")
        assert a != b

    def test_filesystem_safe_chars(self):
        deal_id = _make_deal_id("https://www.groupon.com/deals/ga-great-clips-6")
        # Only lowercase alphanumeric and hyphens
        import re
        assert re.match(r"^[a-z0-9\-]+$", deal_id), f"Unsafe characters in: {deal_id}"

    def test_no_double_hyphens(self):
        deal_id = _make_deal_id("https://www.groupon.com/deals/spa-deal")
        assert "--" not in deal_id

    def test_max_reasonable_length(self):
        url = "https://www.groupon.com/deals/" + "a" * 200
        deal_id = _make_deal_id(url)
        assert len(deal_id) <= 60, f"deal_id too long: {len(deal_id)} chars"

    def test_includes_slug_from_url(self):
        deal_id = _make_deal_id("https://www.groupon.com/deals/escape-room-madness-nyc")
        assert "escape" in deal_id or "room" in deal_id

    def test_fallback_for_unusual_url(self):
        # URL with no /deals/ segment should still produce a valid ID
        deal_id = _make_deal_id("https://www.example.com/offer?id=123")
        assert isinstance(deal_id, str)
        assert len(deal_id) > 0


# ---------------------------------------------------------------------------
# load_deals — from list
# ---------------------------------------------------------------------------

class TestLoadDealsFromList:
    def test_basic_load(self):
        urls = ["https://www.groupon.com/deals/spa-deal"]
        deals = load_deals(urls)
        assert len(deals) == 1
        assert deals[0].url == "https://www.groupon.com/deals/spa-deal"

    def test_deduplication(self):
        urls = [
            "https://www.groupon.com/deals/spa-deal",
            "https://www.groupon.com/deals/spa-deal",  # duplicate
            "https://www.groupon.com/deals/massage-deal",
        ]
        deals = load_deals(urls)
        assert len(deals) == 2

    def test_strips_blank_entries(self):
        urls = ["  ", "", "https://www.groupon.com/deals/spa-deal", "  "]
        deals = load_deals(urls)
        assert len(deals) == 1

    def test_deal_ids_are_unique(self):
        urls = [
            "https://www.groupon.com/deals/spa-chicago",
            "https://www.groupon.com/deals/massage-nyc",
        ]
        deals = load_deals(urls)
        ids = [d.deal_id for d in deals]
        assert len(set(ids)) == len(ids)

    def test_tracking_params_deduplicated(self):
        # Same page, different tracking → should collapse to one deal
        urls = [
            "https://www.groupon.com/deals/spa-deal?utm_source=email",
            "https://www.groupon.com/deals/spa-deal?utm_source=push",
        ]
        deals = load_deals(urls)
        assert len(deals) == 1


# ---------------------------------------------------------------------------
# load_deals — from file
# ---------------------------------------------------------------------------

class TestLoadDealsFromFile:
    def test_load_from_file(self, tmp_path: Path):
        f = tmp_path / "deals.txt"
        f.write_text(
            "# Comment line\n"
            "https://www.groupon.com/deals/spa-deal\n"
            "\n"
            "https://www.groupon.com/deals/massage-deal\n"
        )
        deals = load_deals(f)
        assert len(deals) == 2

    def test_comment_lines_skipped(self, tmp_path: Path):
        f = tmp_path / "deals.txt"
        f.write_text(
            "# This is a comment\n"
            "# Another comment\n"
            "https://www.groupon.com/deals/spa-deal\n"
        )
        deals = load_deals(f)
        assert len(deals) == 1

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_deals(tmp_path / "nonexistent.txt")

    def test_empty_file_returns_empty_list(self, tmp_path: Path):
        f = tmp_path / "deals.txt"
        f.write_text("# only comments\n\n")
        deals = load_deals(f)
        assert deals == []
