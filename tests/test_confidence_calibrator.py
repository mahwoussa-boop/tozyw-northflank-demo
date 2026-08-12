"""tests/test_confidence_calibrator.py — اختبارات معاير الثقة.

يغطّي:
- المعايرة بدون بيانات (إرجاع الثقة الخام).
- المعايرة حسب العلامة التجارية / القسم / الشريحة السعرية / العام.
- أولوية السياقات (الأكثر تحديداً أولاً).
- الاحتياطي عند نقص العينات.
- بوابة الموافقة التلقائية.
- خريطة المعايرة وإعادة الحساب.
"""
from __future__ import annotations

import sqlite3
import tempfile
import os

import pytest

from infrastructure.db_manager import DatabaseManager
from services.learning.feedback_collector import FeedbackCollector
from services.learning.confidence_calibrator import (
    CalibrationResult,
    ConfidenceCalibrator,
)


# ---------------------------------------------------------------------------
# تجهيزات الاختبار
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_path(tmp_path):
    """ينشئ ملف قاعدة بيانات مؤقتة."""
    return str(tmp_path / "test_calibrator.db")


@pytest.fixture()
def db(db_path):
    """ينشئ مدير قاعدة بيانات مؤقتة."""
    return DatabaseManager(db_path)


@pytest.fixture()
def feedback(db):
    """ينشئ جامع تقييمات مع جدول جاهز."""
    fc = FeedbackCollector(db)
    fc.ensure_table()
    return fc


@pytest.fixture()
def calibrator(feedback):
    """ينشئ معاير ثقة."""
    return ConfidenceCalibrator(feedback)


def _seed_records(
    feedback: FeedbackCollector,
    *,
    brand: str = "Dior",
    section: str = "price_raise",
    ai_price: float = 500.0,
    count_approved: int = 0,
    count_rejected: int = 0,
    count_modified: int = 0,
) -> None:
    """يُدخل سجلات تقييم بأعداد محددة لكل نوع إجراء."""
    seq = 1
    for _ in range(count_approved):
        feedback.record(
            decision_id=seq, product_name=f"Product-{seq}",
            section=section, ai_price=ai_price, ai_confidence=0.9,
            action="approved", user_price=ai_price, brand=brand,
        )
        seq += 1
    for _ in range(count_rejected):
        feedback.record(
            decision_id=seq, product_name=f"Product-{seq}",
            section=section, ai_price=ai_price, ai_confidence=0.9,
            action="rejected", user_price=0.0, brand=brand,
        )
        seq += 1
    for _ in range(count_modified):
        feedback.record(
            decision_id=seq, product_name=f"Product-{seq}",
            section=section, ai_price=ai_price, ai_confidence=0.9,
            action="modified", user_price=ai_price * 1.1, brand=brand,
        )
        seq += 1


# ---------------------------------------------------------------------------
# الاختبارات
# ---------------------------------------------------------------------------

class TestCalibrateNoData:
    """المعايرة بدون بيانات تُرجع الثقة الخام."""

    def test_returns_raw_confidence_unchanged(self, calibrator):
        result = calibrator.calibrate(0.85)
        assert result.raw_confidence == 0.85
        assert result.calibrated_confidence == 0.85
        assert result.adjustment_factor == 1.0
        assert result.context_key == "none"
        assert result.sample_size == 0


class TestCalibrateWithBrand:
    """المعايرة حسب العلامة التجارية."""

    def test_brand_data_adjusts_correctly(self, feedback, calibrator):
        # 6 approved, 4 rejected → 60% approval
        _seed_records(feedback, brand="Chanel", section="s1",
                      count_approved=6, count_rejected=4)

        result = calibrator.calibrate(0.90, brand="Chanel")
        assert result.context_key == "brand:Chanel"
        assert result.sample_size == 10
        assert result.historical_accuracy == pytest.approx(0.6, abs=0.01)
        assert result.adjustment_factor == pytest.approx(0.6, abs=0.01)
        assert result.calibrated_confidence == pytest.approx(0.54, abs=0.01)


class TestCalibrateWithSection:
    """المعايرة حسب القسم."""

    def test_section_data_adjusts_correctly(self, feedback, calibrator):
        # 8 approved, 2 rejected → 80% approval
        _seed_records(feedback, brand="NoBrand", section="price_lower",
                      count_approved=8, count_rejected=2)

        result = calibrator.calibrate(0.80, section="price_lower")
        assert result.context_key == "section:price_lower"
        assert result.historical_accuracy == pytest.approx(0.8, abs=0.01)
        assert result.calibrated_confidence == pytest.approx(0.64, abs=0.01)


class TestCalibratePriority:
    """أولوية السياقات: brand+section > brand > section > tier > global."""

    def test_prefers_brand_section_over_brand_alone(self, feedback, calibrator):
        # brand+section "Dior" + "price_raise": 5 approved, 5 rejected → 50%
        _seed_records(feedback, brand="Dior", section="price_raise",
                      count_approved=5, count_rejected=5)
        # brand "Dior" alone also has data in "other_section": 8 approved, 2 rejected
        _seed_records(feedback, brand="Dior", section="other_section",
                      count_approved=8, count_rejected=2)

        result = calibrator.calibrate(
            0.90, brand="Dior", section="price_raise",
        )
        # يجب اختيار brand+section (50%) وليس brand العام
        assert result.context_key == "brand+section:Dior+price_raise"
        assert result.historical_accuracy == pytest.approx(0.5, abs=0.01)
        assert result.calibrated_confidence == pytest.approx(0.45, abs=0.01)


class TestCalibrateFallback:
    """الاحتياطي عند نقص العينات الخاصة."""

    def test_falls_back_to_global_when_specific_insufficient(
        self, feedback, calibrator
    ):
        # brand "Rare" has only 2 records (< 5 min for brand)
        _seed_records(feedback, brand="Rare", section="sec1",
                      count_approved=1, count_rejected=1)
        # global has 10 records from another brand
        _seed_records(feedback, brand="Common", section="sec2",
                      count_approved=7, count_rejected=3)

        result = calibrator.calibrate(0.90, brand="Rare")
        # brand "Rare" has 2 < 5 → skip. global has 12 records.
        # global: 8 approved / 12 total = 66.67%
        assert result.context_key == "global"
        assert result.sample_size == 12


class TestCalibrateEdgeCases:
    """حالات حدّية للمعايرة."""

    def test_100_pct_approval_returns_raw(self, feedback, calibrator):
        _seed_records(feedback, brand="Perfect", section="s1",
                      count_approved=10)
        result = calibrator.calibrate(0.80, brand="Perfect")
        assert result.calibrated_confidence == pytest.approx(0.80, abs=0.001)
        assert result.adjustment_factor == pytest.approx(1.0, abs=0.001)

    def test_50_pct_approval_halves_confidence(self, feedback, calibrator):
        _seed_records(feedback, brand="Half", section="s1",
                      count_approved=5, count_rejected=5)
        result = calibrator.calibrate(1.0, brand="Half")
        assert result.calibrated_confidence == pytest.approx(0.5, abs=0.001)

    def test_0_pct_approval_returns_zero(self, feedback, calibrator):
        _seed_records(feedback, brand="Bad", section="s1",
                      count_rejected=10)
        result = calibrator.calibrate(0.95, brand="Bad")
        assert result.calibrated_confidence == pytest.approx(0.0, abs=0.001)
        assert result.adjustment_factor == pytest.approx(0.0, abs=0.001)


class TestGetCalibrationMap:
    """خريطة المعايرة."""

    def test_returns_all_contexts(self, feedback, calibrator):
        _seed_records(feedback, brand="Alpha", section="price_raise",
                      ai_price=500.0, count_approved=7, count_rejected=3)
        _seed_records(feedback, brand="Beta", section="price_lower",
                      ai_price=50.0, count_approved=4, count_rejected=6)

        cal_map = calibrator.get_calibration_map()

        assert "brand:Alpha" in cal_map
        assert "brand:Beta" in cal_map
        assert "section:price_raise" in cal_map
        assert "section:price_lower" in cal_map
        assert "global" in cal_map
        # brand Alpha: 7/10 = 0.7
        assert cal_map["brand:Alpha"] == pytest.approx(0.7, abs=0.01)


class TestShouldAutoApprove:
    """بوابة الموافقة التلقائية."""

    def test_returns_true_for_high_confidence(self, calibrator):
        assert calibrator.should_auto_approve(0.95) is True

    def test_returns_false_for_low_confidence(self, calibrator):
        assert calibrator.should_auto_approve(0.70) is False

    def test_returns_true_at_exact_threshold(self, calibrator):
        assert calibrator.should_auto_approve(0.90) is True

    def test_custom_threshold(self, calibrator):
        assert calibrator.should_auto_approve(0.80, threshold=0.75) is True
        assert calibrator.should_auto_approve(0.74, threshold=0.75) is False


class TestRecalculate:
    """إعادة حساب خريطة المعايرة."""

    def test_recalculate_updates_map(self, feedback, calibrator):
        _seed_records(feedback, brand="X", section="s1",
                      count_approved=5, count_rejected=5)
        map1 = calibrator.recalculate()
        assert "brand:X" in map1

        # إضافة بيانات جديدة
        _seed_records(feedback, brand="X", section="s1",
                      count_approved=10)
        map2 = calibrator.recalculate()

        # المعدل يجب أن يتغير: كان 50% → الآن 15/20 = 75%
        assert map2["brand:X"] > map1["brand:X"]


class TestCalibrateMinSamples:
    """التحقق من الحد الأدنى للعينات."""

    def test_respects_min_samples_threshold(self, feedback, calibrator):
        # إدخال سجلين فقط (< 3 الحد الأدنى العام)
        _seed_records(feedback, brand="Tiny", section="s1",
                      count_approved=1, count_rejected=1)

        result = calibrator.calibrate(0.85, brand="Tiny")
        # 2 < 5 → skip brand. 2 < 3 → skip global. → none
        assert result.context_key == "none"
        assert result.adjustment_factor == 1.0
        assert result.calibrated_confidence == 0.85


class TestCalibrationResult:
    """اختبارات نموذج CalibrationResult."""

    def test_dataclass_creation(self):
        r = CalibrationResult(
            raw_confidence=0.9,
            calibrated_confidence=0.54,
            historical_accuracy=0.6,
            adjustment_factor=0.6,
            sample_size=10,
            context_key="brand:Test",
        )
        assert r.raw_confidence == 0.9
        assert r.calibrated_confidence == 0.54
        assert r.context_key == "brand:Test"


class TestCalibrateTier:
    """المعايرة حسب الشريحة السعرية."""

    def test_tier_calibration(self, feedback, calibrator):
        # luxury tier: ai_price > 700
        _seed_records(feedback, brand="LuxBrand", section="s1",
                      ai_price=1000.0, count_approved=4, count_rejected=6)

        result = calibrator.calibrate(0.80, price_tier="luxury")
        assert result.context_key == "tier:luxury"
        assert result.historical_accuracy == pytest.approx(0.4, abs=0.01)
        assert result.calibrated_confidence == pytest.approx(0.32, abs=0.01)
