"""tests/test_feedback_collector.py — اختبارات خدمة جمع التقييمات (FeedbackCollector).

يغطّي: إنشاء الجدول، تسجيل التقييمات، حساب الانحراف والشرائح السعرية،
إحصائيات الدقة لكل علامة تجارية وشريحة، التصفية الزمنية والقسمية.
"""
from __future__ import annotations

import tempfile
import os
from datetime import datetime, timedelta, timezone

import pytest

from infrastructure.db_manager import DatabaseManager
from services.learning.feedback_collector import (
    FeedbackCollector,
    FeedbackRecord,
    AccuracyStats,
    BrandFeedbackStats,
    compute_price_tier,
    compute_deviation,
)


# ---------------------------------------------------------------------------
# تجهيز بيئة الاختبار
# ---------------------------------------------------------------------------

@pytest.fixture()
def collector():
    """ينشئ FeedbackCollector مع قاعدة بيانات مؤقتة."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = DatabaseManager(path)
    fc = FeedbackCollector(db)
    fc.ensure_table()
    yield fc
    db.close()
    os.unlink(path)


def _seed(fc: FeedbackCollector, count: int = 5, brand: str = "BrandX",
          section: str = "price_raise", action: str = "approved",
          ai_price: float = 200.0, user_price: float = 200.0) -> list[FeedbackRecord]:
    """مساعد لزرع سجلات تقييم متعددة."""
    records = []
    for i in range(count):
        rec = fc.record(
            decision_id=i + 1,
            product_name=f"Product-{i}",
            section=section,
            ai_price=ai_price,
            ai_confidence=0.85,
            action=action,
            user_price=user_price,
            brand=brand,
        )
        records.append(rec)
    return records


# ---------------------------------------------------------------------------
# 1. إنشاء الجدول
# ---------------------------------------------------------------------------

class TestEnsureTable:
    def test_creates_table(self, collector: FeedbackCollector) -> None:
        """ensure_table ينشئ جدول feedback_records."""
        assert collector._db.table_exists("feedback_records")

    def test_idempotent(self, collector: FeedbackCollector) -> None:
        """استدعاء ensure_table مرتين لا يسبب خطأ."""
        collector.ensure_table()  # مرة ثانية
        assert collector._db.table_exists("feedback_records")


# ---------------------------------------------------------------------------
# 2. تسجيل التقييم
# ---------------------------------------------------------------------------

class TestRecord:
    def test_stores_feedback_correctly(self, collector: FeedbackCollector) -> None:
        """record يحفظ البيانات الأساسية بشكل صحيح."""
        rec = collector.record(
            decision_id=42, product_name="عطر ملكي", section="price_raise",
            ai_price=250.0, ai_confidence=0.9, action="approved",
            user_price=250.0, brand="Arabian",
        )
        assert rec.decision_id == 42
        assert rec.product_name == "عطر ملكي"
        assert rec.brand == "Arabian"
        assert rec.user_action == "approved"
        assert len(rec.record_id) == 12

        # تأكد من أنه مخزّن في قاعدة البيانات
        rows = collector._db.query(
            "SELECT * FROM feedback_records WHERE record_id = ?",
            (rec.record_id,),
        )
        assert len(rows) == 1

    def test_computes_positive_deviation(self, collector: FeedbackCollector) -> None:
        """يحسب الانحراف الموجب عند رفع السعر."""
        rec = collector.record(
            decision_id=1, product_name="P1", section="s1",
            ai_price=100.0, ai_confidence=0.8, action="modified",
            user_price=120.0,
        )
        assert rec.deviation_pct == 20.0

    def test_computes_negative_deviation(self, collector: FeedbackCollector) -> None:
        """يحسب الانحراف السالب عند خفض السعر."""
        rec = collector.record(
            decision_id=2, product_name="P2", section="s1",
            ai_price=200.0, ai_confidence=0.8, action="modified",
            user_price=150.0,
        )
        assert rec.deviation_pct == -25.0

    def test_computes_budget_tier(self, collector: FeedbackCollector) -> None:
        """يصنّف الأسعار < 100 كشريحة budget."""
        rec = collector.record(
            decision_id=3, product_name="P3", section="s1",
            ai_price=50.0, ai_confidence=0.8, action="approved",
        )
        assert rec.price_range_tier == "budget"

    def test_computes_mid_tier(self, collector: FeedbackCollector) -> None:
        """يصنّف الأسعار 100-300 كشريحة mid."""
        rec = collector.record(
            decision_id=4, product_name="P4", section="s1",
            ai_price=200.0, ai_confidence=0.8, action="approved",
        )
        assert rec.price_range_tier == "mid"

    def test_computes_premium_tier(self, collector: FeedbackCollector) -> None:
        """يصنّف الأسعار 300-700 كشريحة premium."""
        rec = collector.record(
            decision_id=5, product_name="P5", section="s1",
            ai_price=500.0, ai_confidence=0.8, action="approved",
        )
        assert rec.price_range_tier == "premium"

    def test_computes_luxury_tier(self, collector: FeedbackCollector) -> None:
        """يصنّف الأسعار > 700 كشريحة luxury."""
        rec = collector.record(
            decision_id=6, product_name="P6", section="s1",
            ai_price=1000.0, ai_confidence=0.8, action="approved",
        )
        assert rec.price_range_tier == "luxury"


# ---------------------------------------------------------------------------
# 3. إحصائيات الدقة
# ---------------------------------------------------------------------------

class TestAccuracyStats:
    def test_returns_correct_counts(self, collector: FeedbackCollector) -> None:
        """get_accuracy_stats يحسب العدد الإجمالي والموافقات والرفض والتعديلات."""
        _seed(collector, count=3, action="approved")
        _seed(collector, count=2, action="rejected", brand="BrandY")
        _seed(collector, count=1, action="modified", brand="BrandZ",
              ai_price=100.0, user_price=110.0)

        stats = collector.get_accuracy_stats(window_days=365)
        assert stats.total_decisions == 6
        assert stats.approved_count == 3
        assert stats.rejected_count == 2
        assert stats.modified_count == 1
        assert stats.approval_rate == 50.0

    def test_window_filtering(self, collector: FeedbackCollector) -> None:
        """get_accuracy_stats يحترم النافذة الزمنية."""
        # زرع سجلات حديثة
        _seed(collector, count=2, action="approved")

        # إدراج سجل قديم مباشرة
        old_date = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        collector._db.execute(
            """INSERT INTO feedback_records
               (record_id, decision_id, product_name, brand, section,
                ai_suggested_price, ai_confidence, user_action,
                user_final_price, deviation_pct, price_range_tier, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("old_record_01", 999, "OldProduct", "OldBrand", "sec",
             100.0, 0.5, "rejected", 0.0, 0.0, "mid", old_date),
        )

        stats_30 = collector.get_accuracy_stats(window_days=30)
        assert stats_30.total_decisions == 2  # فقط الحديثة

        stats_90 = collector.get_accuracy_stats(window_days=90)
        assert stats_90.total_decisions == 3  # الكل

    def test_empty_stats(self, collector: FeedbackCollector) -> None:
        """get_accuracy_stats يعيد إحصائيات فارغة عند عدم وجود بيانات."""
        stats = collector.get_accuracy_stats()
        assert stats.total_decisions == 0
        assert stats.approval_rate == 0.0
        assert stats.best_brand is None
        assert stats.worst_brand is None


# ---------------------------------------------------------------------------
# 4. إحصائيات العلامات التجارية
# ---------------------------------------------------------------------------

class TestBrandStats:
    def test_returns_correct_data(self, collector: FeedbackCollector) -> None:
        """get_brand_stats يحسب بيانات العلامة التجارية بدقة."""
        _seed(collector, count=3, brand="Nike", action="approved")
        _seed(collector, count=2, brand="Nike", action="rejected")

        stats = collector.get_brand_stats("Nike")
        assert stats.brand == "Nike"
        assert stats.total == 5
        assert stats.approved == 3
        assert stats.rejected == 2
        assert stats.approval_rate == 60.0

    def test_unknown_brand(self, collector: FeedbackCollector) -> None:
        """get_brand_stats يعيد إحصائيات فارغة لعلامة غير موجودة."""
        stats = collector.get_brand_stats("NonExistent")
        assert stats.total == 0
        assert stats.approval_rate == 0.0

    def test_all_brand_stats_respects_min(self, collector: FeedbackCollector) -> None:
        """get_all_brand_stats يحترم الحد الأدنى لعدد القرارات."""
        _seed(collector, count=5, brand="PopularBrand", action="approved")
        _seed(collector, count=1, brand="RareBrand", action="approved")

        all_stats = collector.get_all_brand_stats(min_decisions=3)
        brands = [s.brand for s in all_stats]
        assert "PopularBrand" in brands
        assert "RareBrand" not in brands


# ---------------------------------------------------------------------------
# 5. استعلامات
# ---------------------------------------------------------------------------

class TestQueries:
    def test_get_recent_sorted_desc(self, collector: FeedbackCollector) -> None:
        """get_recent يعيد السجلات مرتبة بالتاريخ تنازلياً."""
        records = _seed(collector, count=5)
        recent = collector.get_recent(limit=3)
        assert len(recent) == 3
        # التأكد من الترتيب التنازلي
        dates = [r.created_at for r in recent]
        assert dates == sorted(dates, reverse=True)

    def test_get_by_section_filters(self, collector: FeedbackCollector) -> None:
        """get_by_section يصفّي حسب القسم."""
        _seed(collector, count=3, section="price_raise")
        _seed(collector, count=2, section="approved")

        raise_recs = collector.get_by_section("price_raise")
        assert len(raise_recs) == 3
        assert all(r.section == "price_raise" for r in raise_recs)

    def test_get_approval_rate_by_tier(self, collector: FeedbackCollector) -> None:
        """get_approval_rate_by_tier يحسب المعدل لكل شريحة."""
        # budget: 2 approved out of 2
        _seed(collector, count=2, action="approved", ai_price=50.0)
        # mid: 1 approved, 1 rejected out of 2
        _seed(collector, count=1, action="approved", ai_price=200.0, brand="B1")
        _seed(collector, count=1, action="rejected", ai_price=200.0, brand="B2")

        rates = collector.get_approval_rate_by_tier()
        assert rates["budget"] == 100.0
        assert rates["mid"] == 50.0


# ---------------------------------------------------------------------------
# 6. دوال مساعدة
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_compute_price_tier_all(self) -> None:
        """compute_price_tier يصنّف كل الشرائح بدقة."""
        assert compute_price_tier(0) == "budget"
        assert compute_price_tier(99.99) == "budget"
        assert compute_price_tier(100) == "mid"
        assert compute_price_tier(300) == "mid"
        assert compute_price_tier(300.01) == "premium"
        assert compute_price_tier(700) == "premium"
        assert compute_price_tier(700.01) == "luxury"
        assert compute_price_tier(5000) == "luxury"

    def test_compute_deviation_normal(self) -> None:
        """compute_deviation يحسب الانحراف بشكل صحيح."""
        assert compute_deviation(100.0, 120.0) == 20.0
        assert compute_deviation(200.0, 150.0) == -25.0
        assert compute_deviation(100.0, 100.0) == 0.0

    def test_compute_deviation_zero_ai_price(self) -> None:
        """compute_deviation يعيد 0.0 عندما يكون سعر الذكاء الاصطناعي صفراً."""
        assert compute_deviation(0.0, 50.0) == 0.0
        assert compute_deviation(0.0, 0.0) == 0.0

    def test_feedback_record_to_dict_roundtrip(self) -> None:
        """FeedbackRecord.to_dict و from_dict يحافظان على البيانات."""
        now = datetime.now(timezone.utc)
        rec = FeedbackRecord(
            record_id="abc123def456",
            decision_id=10,
            product_name="Test",
            brand="TestBrand",
            section="approved",
            ai_suggested_price=150.0,
            ai_confidence=0.75,
            user_action="approved",
            user_final_price=150.0,
            deviation_pct=0.0,
            price_range_tier="mid",
            created_at=now,
        )
        d = rec.to_dict()
        restored = FeedbackRecord.from_dict(d)
        assert restored.record_id == rec.record_id
        assert restored.decision_id == rec.decision_id
        assert restored.brand == rec.brand
        assert restored.created_at.isoformat() == now.isoformat()
