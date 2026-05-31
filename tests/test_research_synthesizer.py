"""
Tests for ai/research_synthesizer.py — pure functions only (no API calls).

Covers _reconcile_deal_quality, which normalises the AI's quality score
and label to be consistent with its own value verdict + review signals.
"""

from __future__ import annotations

import pytest

from ai.research_synthesizer import _reconcile_deal_quality
from models import DealAudit, ResearchSynthesis


def _synthesis(
    value_assessment: str,
    deal_quality: str,
    deal_quality_score: float,
    review_themes=None,
) -> ResearchSynthesis:
    return ResearchSynthesis(
        value_assessment=value_assessment,
        value_reasoning="test reasoning",
        merchant_differentiators=[],
        red_flags=[],
        deal_quality=deal_quality,
        deal_quality_score=deal_quality_score,
        review_themes=review_themes or [],
    )


def _audit(rating: float = None, review_count: int = None) -> DealAudit:
    return DealAudit(
        deal_id="test",
        url="https://www.groupon.com/deals/test",
        reviews_avg_rating=rating,
        reviews_count=review_count,
    )


class TestReconcileDealQuality:
    # --- Value verdict floors / ceilings ---

    def test_genuine_deal_floors_score_at_7(self):
        s = _synthesis("genuine_deal", "average", 5.0)
        _reconcile_deal_quality(s, _audit())
        assert s.deal_quality_score >= 7.0

    def test_genuine_deal_assigns_strong_or_good_label(self):
        s = _synthesis("genuine_deal", "weak", 3.0)
        _reconcile_deal_quality(s, _audit())
        assert s.deal_quality in ("strong", "good")

    def test_overpriced_caps_score_at_4(self):
        s = _synthesis("overpriced", "strong", 9.0)
        _reconcile_deal_quality(s, _audit())
        assert s.deal_quality_score <= 4.0

    def test_overpriced_assigns_weak_label(self):
        s = _synthesis("overpriced", "strong", 9.0)
        _reconcile_deal_quality(s, _audit())
        assert s.deal_quality in ("average", "weak")

    def test_marginal_clamps_to_4_6(self):
        s = _synthesis("marginal", "strong", 8.0)
        _reconcile_deal_quality(s, _audit())
        assert 4.0 <= s.deal_quality_score <= 6.0

    def test_marginal_low_score_raised_to_4(self):
        s = _synthesis("marginal", "weak", 2.0)
        _reconcile_deal_quality(s, _audit())
        assert s.deal_quality_score >= 4.0

    # --- Review signal floors ---

    def test_high_rating_high_volume_floors_at_7_5(self):
        s = _synthesis("marginal", "average", 5.0)
        _reconcile_deal_quality(s, _audit(rating=4.9, review_count=600))
        # Review floor overrides marginal ceiling → check the higher floor wins
        assert s.deal_quality_score >= 7.5

    def test_good_rating_good_volume_floors_at_7(self):
        s = _synthesis("marginal", "average", 5.0)
        _reconcile_deal_quality(s, _audit(rating=4.6, review_count=250))
        assert s.deal_quality_score >= 7.0

    def test_low_rating_no_floor_applied(self):
        s = _synthesis("marginal", "average", 5.0)
        _reconcile_deal_quality(s, _audit(rating=3.0, review_count=50))
        # No review floor — score stays in marginal range
        assert 4.0 <= s.deal_quality_score <= 6.0

    def test_no_rating_data_no_floor(self):
        s = _synthesis("genuine_deal", "average", 5.0)
        _reconcile_deal_quality(s, _audit(rating=None, review_count=None))
        # Only verdict floor applies
        assert s.deal_quality_score >= 7.0

    # --- Label bands ---

    def test_score_8_is_strong(self):
        s = _synthesis("genuine_deal", "average", 8.0)
        _reconcile_deal_quality(s, _audit())
        assert s.deal_quality == "strong"

    def test_score_7_is_good(self):
        s = _synthesis("genuine_deal", "average", 7.0)
        _reconcile_deal_quality(s, _audit())
        # 7.0 is exactly the boundary — should be strong or good depending on final score
        assert s.deal_quality in ("strong", "good")

    def test_score_5_is_average(self):
        s = _synthesis("marginal", "average", 5.0)
        _reconcile_deal_quality(s, _audit())
        assert s.deal_quality == "average"

    def test_score_2_is_weak(self):
        s = _synthesis("overpriced", "weak", 2.0)
        _reconcile_deal_quality(s, _audit())
        assert s.deal_quality == "weak"

    # --- Score clamping ---

    def test_score_never_exceeds_10(self):
        s = _synthesis("genuine_deal", "strong", 15.0)
        _reconcile_deal_quality(s, _audit(rating=4.9, review_count=1000))
        assert s.deal_quality_score <= 10.0

    def test_score_never_below_1(self):
        s = _synthesis("overpriced", "weak", -5.0)
        _reconcile_deal_quality(s, _audit())
        assert s.deal_quality_score >= 1.0

    # --- Label/score consistency after reconciliation ---

    def test_label_matches_score_band_after_reconciliation(self):
        """After reconciliation, label and score should tell the same story."""
        for verdict, init_score in [
            ("genuine_deal", 3.0),
            ("overpriced", 9.0),
            ("marginal", 7.0),
        ]:
            s = _synthesis(verdict, "average", init_score)
            _reconcile_deal_quality(s, _audit())
            score = s.deal_quality_score
            if score >= 7.5:
                assert s.deal_quality == "strong"
            elif score >= 6.5:
                assert s.deal_quality == "good"
            elif score >= 4.0:
                assert s.deal_quality == "average"
            else:
                assert s.deal_quality == "weak"
