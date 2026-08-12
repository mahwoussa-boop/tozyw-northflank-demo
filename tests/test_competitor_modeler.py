"""tests/test_competitor_modeler.py — اختبارات شاملة لخدمة نمذجة سلوك المنافسين.

يغطي:
- إنشاء الجدول
- بناء الملفات التعريفية (عدواني، متميز، متقلب، معتدل)
- التخزين واسترجاع البيانات
- التنبؤ بردود الأفعال
- ترتيب المنافسين حسب التهديد
- تحويل to_dict
"""
from __future__ import annotations

import math
import os
import tempfile
from datetime import datetime

import pytest

from infrastructure.db_manager import DatabaseManager
from services.intelligence.competitor_modeler import (
    CompetitorBehaviorModeler,
    CompetitorPrediction,
    CompetitorProfile,
)


# ════════════════════════════════════════════════════════════════════
#  تجهيزات الاختبار (Fixtures)
# ════════════════════════════════════════════════════════════════════

@pytest.fixture()
def db(tmp_path):
    """ينشئ قاعدة بيانات مؤقتة لكل اختبار."""
    db_path = str(tmp_path / "test_competitor.db")
    manager = DatabaseManager(db_path)
    yield manager
    manager.close()


@pytest.fixture()
def modeler(db):
    """ينشئ خدمة النمذجة مع إعداد الجدول."""
    m = CompetitorBehaviorModeler(db)
    m.ensure_table()
    return m


# ════════════════════════════════════════════════════════════════════
#  الاختبارات
# ════════════════════════════════════════════════════════════════════

class TestEnsureTable:
    """اختبارات إنشاء الجدول."""

    def test_creates_table(self, db):
        """يتحقق أن ensure_table ينشئ الجدول بنجاح."""
        modeler = CompetitorBehaviorModeler(db)
        assert not db.table_exists("competitor_profiles")
        modeler.ensure_table()
        assert db.table_exists("competitor_profiles")

    def test_idempotent(self, db):
        """يتحقق أن استدعاء ensure_table مرتين لا يسبب خطأ."""
        modeler = CompetitorBehaviorModeler(db)
        modeler.ensure_table()
        modeler.ensure_table()  # لا يجب أن يرمي استثناء
        assert db.table_exists("competitor_profiles")


class TestBuildProfile:
    """اختبارات بناء الملف التعريفي."""

    def test_aggressive_competitor(self, modeler):
        """منافس أرخص بأكثر من 10% يُصنّف عدوانياً."""
        # سعرنا 100، سعرهم 85 → فرق -15%
        pairs = [(100.0, 85.0), (200.0, 170.0), (150.0, 127.5)]
        profile = modeler.build_profile("متجر_عدواني", pairs)

        assert profile.pricing_strategy == "aggressive"
        assert profile.avg_price_delta_pct < -10.0
        assert profile.price_count == 3
        assert profile.store_name == "متجر_عدواني"

    def test_premium_competitor(self, modeler):
        """منافس أغلى بأكثر من 10% يُصنّف متميزاً."""
        # سعرنا 100، سعرهم 115 → فرق +15%
        pairs = [(100.0, 115.0), (200.0, 230.0), (50.0, 57.5)]
        profile = modeler.build_profile("متجر_متميز", pairs)

        assert profile.pricing_strategy == "premium"
        assert profile.avg_price_delta_pct > 10.0

    def test_erratic_competitor(self, modeler):
        """منافس بأسعار متقلبة (CV > 0.3) يُصنّف متقلباً."""
        # تباين كبير: بعض الأسعار أقل وبعضها أعلى بكثير
        pairs = [
            (100.0, 60.0),   # ratio 0.6
            (100.0, 140.0),  # ratio 1.4
            (100.0, 50.0),   # ratio 0.5
            (100.0, 150.0),  # ratio 1.5
        ]
        profile = modeler.build_profile("متجر_متقلب", pairs)

        assert profile.pricing_strategy == "erratic"
        assert profile.consistency_score < 0.7

    def test_moderate_competitor(self, modeler):
        """منافس بأسعار مشابهة (ضمن ±10%) يُصنّف معتدلاً."""
        # سعرنا 100، سعرهم ~100 → فرق ~0%
        pairs = [(100.0, 102.0), (200.0, 198.0), (150.0, 151.0)]
        profile = modeler.build_profile("متجر_معتدل", pairs)

        assert profile.pricing_strategy == "moderate"
        assert -10.0 <= profile.avg_price_delta_pct <= 10.0

    def test_stores_in_db(self, modeler):
        """يتحقق أن build_profile يحفظ البيانات في قاعدة البيانات."""
        pairs = [(100.0, 90.0), (200.0, 175.0)]
        modeler.build_profile("متجر_حفظ", pairs)

        profile = modeler.get_profile("متجر_حفظ")
        assert profile is not None
        assert profile.store_name == "متجر_حفظ"
        assert profile.price_count == 2

    def test_updates_existing_profile(self, modeler):
        """يتحقق أن إعادة بناء الملف التعريفي تحدّث البيانات الموجودة."""
        pairs_v1 = [(100.0, 85.0)]
        modeler.build_profile("متجر_تحديث", pairs_v1)

        pairs_v2 = [(100.0, 115.0), (200.0, 230.0)]
        modeler.build_profile("متجر_تحديث", pairs_v2)

        profile = modeler.get_profile("متجر_تحديث")
        assert profile is not None
        assert profile.price_count == 2  # تحديث وليس إضافة

    def test_empty_pairs_raises(self, modeler):
        """يتحقق أن القائمة الفارغة ترمي ValueError."""
        with pytest.raises(ValueError, match="فارغة"):
            modeler.build_profile("متجر", [])

    def test_zero_our_price_raises(self, modeler):
        """يتحقق أن سعرنا = 0 يرمي ValueError."""
        with pytest.raises(ValueError, match="موجباً"):
            modeler.build_profile("متجر", [(0.0, 50.0)])

    def test_single_pair(self, modeler):
        """يتحقق أن زوج واحد يعمل بشكل صحيح (CV=0)."""
        profile = modeler.build_profile("متجر_واحد", [(100.0, 95.0)])
        assert profile.price_count == 1
        assert profile.consistency_score == 1.0  # CV=0 → consistency=1
        assert profile.min_price_ratio == profile.max_price_ratio

    def test_ratios_calculated_correctly(self, modeler):
        """يتحقق من صحة حساب نسب الأسعار."""
        pairs = [(100.0, 80.0), (100.0, 120.0)]
        profile = modeler.build_profile("متجر_نسب", pairs)

        assert profile.min_price_ratio == pytest.approx(0.8, abs=0.01)
        assert profile.max_price_ratio == pytest.approx(1.2, abs=0.01)


class TestPredictResponse:
    """اختبارات التنبؤ بردود أفعال المنافسين."""

    def test_aggressive_we_lower(self, modeler):
        """المنافس العدواني يقوّض عند تخفيض أسعارنا."""
        modeler.build_profile("عدواني", [(100.0, 85.0)] * 5)
        pred = modeler.predict_response("عدواني", -10.0)

        assert pred.predicted_response == "undercut"
        assert pred.confidence > 0.5

    def test_aggressive_we_raise(self, modeler):
        """المنافس العدواني يطابق جزئياً عند رفع أسعارنا."""
        modeler.build_profile("عدواني_رفع", [(100.0, 85.0)] * 5)
        pred = modeler.predict_response("عدواني_رفع", 10.0)

        assert pred.predicted_response == "match"

    def test_premium_ignores(self, modeler):
        """المنافس المتميز يتجاهل تغييرات أسعارنا."""
        modeler.build_profile("متميز", [(100.0, 115.0)] * 5)
        pred = modeler.predict_response("متميز", -15.0)

        assert pred.predicted_response == "ignore"
        assert pred.confidence > 0.5

    def test_erratic_low_confidence(self, modeler):
        """المنافس المتقلب ينتج تنبؤاً بثقة منخفضة."""
        pairs = [(100.0, 60.0), (100.0, 140.0), (100.0, 50.0), (100.0, 150.0)]
        modeler.build_profile("متقلب", pairs)
        pred = modeler.predict_response("متقلب", -5.0)

        assert pred.confidence <= 0.4
        assert len(pred.reasoning) > 0

    def test_moderate_response(self, modeler):
        """المنافس المعتدل يستجيب بشكل متناسب."""
        modeler.build_profile("معتدل", [(100.0, 102.0)] * 5)
        pred = modeler.predict_response("معتدل", -10.0)

        assert pred.predicted_response == "match"
        assert pred.store_name == "معتدل"

    def test_unknown_store_raises(self, modeler):
        """التنبؤ لمنافس غير معروف يرمي ValueError."""
        with pytest.raises(ValueError, match="لا يوجد"):
            modeler.predict_response("غير_موجود", -5.0)

    def test_prediction_has_arabic_reasoning(self, modeler):
        """يتحقق أن التنبؤ يحتوي على تفسير بالعربية."""
        modeler.build_profile("عربي", [(100.0, 115.0)] * 3)
        pred = modeler.predict_response("عربي", 5.0)

        assert len(pred.reasoning) > 0
        # تحقق من وجود حروف عربية
        assert any("\u0600" <= c <= "\u06ff" for c in pred.reasoning)


class TestGetProfiles:
    """اختبارات استرجاع الملفات التعريفية."""

    def test_get_profile_returns_correct_data(self, modeler):
        """يتحقق أن get_profile يعيد البيانات الصحيحة."""
        pairs = [(100.0, 90.0), (200.0, 180.0)]
        original = modeler.build_profile("استرجاع", pairs)

        retrieved = modeler.get_profile("استرجاع")
        assert retrieved is not None
        assert retrieved.store_name == original.store_name
        assert retrieved.pricing_strategy == original.pricing_strategy
        assert retrieved.avg_price_delta_pct == pytest.approx(
            original.avg_price_delta_pct, abs=0.01
        )
        assert retrieved.price_count == original.price_count

    def test_get_profile_nonexistent(self, modeler):
        """يتحقق أن get_profile يعيد None لمنافس غير موجود."""
        assert modeler.get_profile("لا_يوجد") is None

    def test_get_all_profiles(self, modeler):
        """يتحقق أن get_all_profiles يعيد جميع الملفات."""
        modeler.build_profile("أ", [(100.0, 90.0)])
        modeler.build_profile("ب", [(100.0, 110.0)])
        modeler.build_profile("ج", [(100.0, 100.0)])

        profiles = modeler.get_all_profiles()
        names = {p.store_name for p in profiles}
        assert names == {"أ", "ب", "ج"}


class TestRankCompetitors:
    """اختبارات ترتيب المنافسين حسب التهديد."""

    def test_rank_by_threat(self, modeler):
        """يتحقق أن الترتيب يضع العدواني أولاً والمتميز أخيراً."""
        # عدواني
        modeler.build_profile("عدو", [(100.0, 85.0)] * 5)
        # متميز
        modeler.build_profile("فاخر", [(100.0, 115.0)] * 5)
        # معتدل
        modeler.build_profile("وسط", [(100.0, 100.0)] * 5)

        ranked = modeler.rank_competitors()
        strategies = [p.pricing_strategy for p in ranked]

        assert strategies.index("aggressive") < strategies.index("moderate")
        assert strategies.index("moderate") < strategies.index("premium")


class TestToDict:
    """اختبارات تحويل to_dict."""

    def test_to_dict_roundtrip(self, modeler):
        """يتحقق أن to_dict ينتج قاموساً كاملاً قابلاً للتسلسل."""
        pairs = [(100.0, 90.0), (200.0, 180.0)]
        profile = modeler.build_profile("قاموس", pairs)
        d = profile.to_dict()

        assert isinstance(d, dict)
        assert d["store_name"] == "قاموس"
        assert d["pricing_strategy"] == profile.pricing_strategy
        assert d["avg_price_delta_pct"] == profile.avg_price_delta_pct
        assert d["price_count"] == 2
        assert "min_price_ratio" in d
        assert "max_price_ratio" in d
        assert "consistency_score" in d
        assert "last_updated" in d

        # التحقق من إمكانية تحويل last_updated مرة أخرى
        dt = datetime.fromisoformat(d["last_updated"])
        assert isinstance(dt, datetime)

    def test_to_dict_all_keys_present(self):
        """يتحقق أن جميع المفاتيح موجودة في القاموس."""
        profile = CompetitorProfile(store_name="اختبار")
        d = profile.to_dict()
        expected_keys = {
            "store_name", "pricing_strategy", "avg_price_delta_pct",
            "price_count", "min_price_ratio", "max_price_ratio",
            "consistency_score", "last_updated",
        }
        assert set(d.keys()) == expected_keys
