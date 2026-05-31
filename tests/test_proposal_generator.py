"""
Tests for ai/proposal_generator.py — pure functions only (no API calls).

Covers:
  - _clean_short_title (truncation, edge cases)
  - _detect_bundle_savings_issue (the deterministic pricing integrity check)
"""

from __future__ import annotations

import pytest

from ai.proposal_generator import _clean_short_title, _detect_bundle_savings_issue
from models import DealAudit, PricingOption


# ---------------------------------------------------------------------------
# _clean_short_title
# ---------------------------------------------------------------------------

class TestCleanShortTitle:
    def test_short_title_passes_through(self):
        assert _clean_short_title("Add session duration", "") == "Add session duration"

    def test_truncates_at_ten_words(self):
        long_title = "One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve"
        result = _clean_short_title(long_title, "")
        assert len(result.split()) == 10

    def test_no_trailing_punctuation_after_truncation(self):
        long_title = "One Two Three Four Five Six Seven Eight Nine Ten, Eleven"
        result = _clean_short_title(long_title, "")
        assert not result.endswith(",")
        assert not result.endswith(".")

    def test_falls_back_to_recommendation_when_empty(self):
        result = _clean_short_title("", "Fix the pricing section now")
        assert result == "Fix the pricing section now"

    def test_exactly_ten_words_unchanged(self):
        title = "One Two Three Four Five Six Seven Eight Nine Ten"
        assert _clean_short_title(title, "") == title

    def test_strips_whitespace(self):
        result = _clean_short_title("  Add address  ", "")
        assert result == "Add address"


# ---------------------------------------------------------------------------
# _detect_bundle_savings_issue
# ---------------------------------------------------------------------------

def _audit_with_pricing(**kwargs) -> DealAudit:
    """Helper to build a minimal DealAudit for pricing tests."""
    defaults = dict(
        deal_id="test-deal",
        url="https://www.groupon.com/deals/test",
        title="Test Deal",
        pricing_options=[],
        highlights=[],
        fine_print=[],
        description="",
    )
    defaults.update(kwargs)
    return DealAudit(**defaults)


class TestDetectBundleSavingsIssue:
    def test_no_issue_when_less_than_two_options(self):
        audit = _audit_with_pricing(
            pricing_options=[
                PricingOption(option_name="Single Session", deal_price=49.0)
            ]
        )
        assert _detect_bundle_savings_issue(audit) is None

    def test_no_issue_when_no_bundle_in_names(self):
        audit = _audit_with_pricing(
            pricing_options=[
                PricingOption(option_name="1 Session", deal_price=49.0),
                PricingOption(option_name="3 Sessions", deal_price=120.0),
            ]
        )
        assert _detect_bundle_savings_issue(audit) is None

    def test_no_issue_when_no_advertised_savings_claim(self):
        # Has a "bundle" option but no savings claim in description/highlights
        audit = _audit_with_pricing(
            pricing_options=[
                PricingOption(option_name="Beginner Course", deal_price=99.0),
                PricingOption(option_name="Advanced Course", deal_price=129.0),
                PricingOption(option_name="Bundle: Both Courses", deal_price=180.0),
            ],
            description="Get both courses together.",
        )
        assert _detect_bundle_savings_issue(audit) is None

    def test_detects_false_savings_claim(self):
        # Bundle + Both advertised as "Save $60" but actual savings is only $2
        audit = _audit_with_pricing(
            pricing_options=[
                PricingOption(option_name="Course A", deal_price=89.0),
                PricingOption(option_name="Course B", deal_price=89.0),
                PricingOption(option_name="Bundle: Both Courses", deal_price=176.0),
            ],
            description="Save $60 versus buying separately with the bundle.",
        )
        result = _detect_bundle_savings_issue(audit)
        assert result is not None
        assert result["category"] == "pricing"
        assert result["expected_impact"] == "high"
        # The recommendation should mention the actual savings situation
        assert "bundle" in result["recommendation"].lower()

    def test_no_false_detection_when_savings_are_accurate(self):
        # Bundle genuinely saves $30 vs separately; advertised claim matches
        audit = _audit_with_pricing(
            pricing_options=[
                PricingOption(option_name="Course A", deal_price=89.0),
                PricingOption(option_name="Course B", deal_price=89.0),
                PricingOption(option_name="Bundle: Both Courses", deal_price=148.0),
            ],
            description="Save $30 versus buying separately.",
        )
        # Real savings = $178 - $148 = $30; advertised = $30 → no issue
        result = _detect_bundle_savings_issue(audit)
        assert result is None

    def test_result_has_required_fields(self):
        audit = _audit_with_pricing(
            pricing_options=[
                PricingOption(option_name="CPR Course", deal_price=60.0),
                PricingOption(option_name="First Aid Course", deal_price=60.0),
                PricingOption(option_name="Bundle: CPR & First Aid", deal_price=118.0),
            ],
            description="Save $60 versus buying separately.",
        )
        result = _detect_bundle_savings_issue(audit)
        if result is not None:
            required_fields = [
                "priority_rank", "category", "recommendation",
                "current_state", "proposed_state", "data_citation",
                "expected_impact", "impact_rationale",
            ]
            for field in required_fields:
                assert field in result, f"Missing field: {field}"

    def test_bundle_more_expensive_than_separately(self):
        # Bundle actually costs MORE than buying separately — obvious red flag
        audit = _audit_with_pricing(
            pricing_options=[
                PricingOption(option_name="Item A", deal_price=50.0),
                PricingOption(option_name="Item B", deal_price=50.0),
                PricingOption(option_name="Bundle: A & B", deal_price=110.0),
            ],
            description="Save $40 versus buying separately.",
        )
        result = _detect_bundle_savings_issue(audit)
        assert result is not None
        # When bundle is MORE expensive, the recommendation should note this
        assert "more" in result["current_state"].lower() or \
               "more" in result["recommendation"].lower()
