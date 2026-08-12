"""tests/test_pattern_memory.py — اختبارات خدمة ذاكرة الأنماط (PatternMemory).

يغطّي: إنشاء الجدول، اكتشاف أنماط العلامات التجارية والحساسية السعرية
والانحياز القسمي، التصفية والاسترجاع، التنسيق العربي، الحذف والعدّ،
والتحويل بين القاموس والكائن.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone

import pytest

from infrastructure.db_manager import DatabaseManager
from services.learning.feedback_collector import FeedbackCollector
from services.learning.pattern_memory import PatternMemory, UserPattern


# ---------------------------------------------------------------------------
# تجهيز بيئة الاختبار
# ---------------------------------------------------------------------------

@pytest.fixture()
def env():
    """ينشئ بيئة اختبار كاملة مع قاعدة بيانات مؤقتة."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = DatabaseManager(path)
    fc = FeedbackCollector(db)
    fc.ensure_table()
    pm = PatternMemory(fc, db)
    pm.ensure_table()
    yield {"db": db, "fc": fc, "pm": pm, "path": path}
    db.close()
    os.unlink(path)


def _seed_brand(fc: FeedbackCollector, brand: str, count: int,
                action: str = "approved", section: str = "price_raise",
                ai_price: float = 200.0, user_price: float = 200.0) -> None:
    """مساعد لزرع سجلات تقييم لعلامة تجارية محددة."""
    for i in range(count):
        fc.record(
            decision_id=hash(f"{brand}-{action}-{i}") % 100_000,
            product_name=f"{brand}-Product-{i}",
            section=section,
            ai_price=ai_price,
            ai_confidence=0.85,
            action=action,
            user_price=user_price,
            brand=brand,
        )


def _seed_section(fc: FeedbackCollector, section: str, count: int,
                  action: str = "approved", brand: str = "Generic",
                  ai_price: float = 200.0, user_price: float = 200.0) -> None:
    """مساعد لزرع سجلات تقييم لقسم محدد."""
    for i in range(count):
        fc.record(
            decision_id=hash(f"{section}-{action}-{i}") % 100_000,
            product_name=f"Section-{section}-{i}",
            section=section,
            ai_price=ai_price,
            ai_confidence=0.8,
            action=action,
            user_price=user_price,
            brand=brand,
        )


# ---------------------------------------------------------------------------
# 1. إنشاء الجدول
# ---------------------------------------------------------------------------

class TestEnsureTable:
    def test_creates_table(self, env) -> None:
        """ensure_table ينشئ جدول user_patterns."""
        assert env["db"].table_exists("user_patterns")

    def test_idempotent(self, env) -> None:
        """استدعاء ensure_table مرتين لا يسبب خطأ."""
        env["pm"].ensure_table()
        assert env["db"].table_exists("user_patterns")


# ---------------------------------------------------------------------------
# 2. اكتشاف الأنماط
# ---------------------------------------------------------------------------

class TestDiscoverPatterns:
    def test_discovers_brand_preference_approve(self, env) -> None:
        """يكتشف نمط موافقة عالية لعلامة تجارية."""
        fc, pm = env["fc"], env["pm"]
        # 9 approved, 1 rejected => 90% approval
        _seed_brand(fc, "Dior", 9, action="approved")
        _seed_brand(fc, "Dior", 1, action="rejected")

        patterns = pm.discover_patterns(min_samples=5, min_confidence=0.7)

        brand_patterns = [p for p in patterns if p.pattern_type == "brand_preference"
                          and p.condition.get("brand") == "Dior"]
        assert len(brand_patterns) >= 1
        p = brand_patterns[0]
        assert p.condition["preference"] == "approve"
        assert p.approval_rate > 0.8
        assert p.sample_count == 10

    def test_discovers_brand_preference_reject(self, env) -> None:
        """يكتشف نمط رفض عالي لعلامة تجارية."""
        fc, pm = env["fc"], env["pm"]
        # 1 approved, 9 rejected => 10% approval
        _seed_brand(fc, "BadBrand", 1, action="approved")
        _seed_brand(fc, "BadBrand", 9, action="rejected")

        patterns = pm.discover_patterns(min_samples=5, min_confidence=0.7)

        brand_patterns = [p for p in patterns if p.pattern_type == "brand_preference"
                          and p.condition.get("brand") == "BadBrand"]
        assert len(brand_patterns) >= 1
        p = brand_patterns[0]
        assert p.condition["preference"] == "reject"
        assert p.approval_rate < 0.3

    def test_respects_min_samples(self, env) -> None:
        """يحترم الحد الأدنى لعدد العينات."""
        fc, pm = env["fc"], env["pm"]
        # فقط 3 سجلات — أقل من min_samples=5
        _seed_brand(fc, "FewBrand", 3, action="approved")

        patterns = pm.discover_patterns(min_samples=5, min_confidence=0.7)

        brand_patterns = [p for p in patterns if p.condition.get("brand") == "FewBrand"]
        assert len(brand_patterns) == 0

    def test_respects_min_confidence(self, env) -> None:
        """يحترم الحد الأدنى للثقة."""
        fc, pm = env["fc"], env["pm"]
        # 4 approved, 3 rejected => 57% approval — بين 0.3 و 0.8
        _seed_brand(fc, "MixedBrand", 4, action="approved")
        _seed_brand(fc, "MixedBrand", 3, action="rejected")

        patterns = pm.discover_patterns(min_samples=5, min_confidence=0.7)

        brand_patterns = [p for p in patterns if p.condition.get("brand") == "MixedBrand"]
        assert len(brand_patterns) == 0

    def test_discovers_section_bias(self, env) -> None:
        """يكتشف نمط انحياز قسمي."""
        fc, pm = env["fc"], env["pm"]
        # قسم price_raise: كل القرارات approved
        _seed_section(fc, "price_raise", 8, action="approved")

        patterns = pm.discover_patterns(min_samples=5, min_confidence=0.7)

        section_patterns = [p for p in patterns if p.pattern_type == "section_bias"
                            and p.condition.get("section") == "price_raise"]
        assert len(section_patterns) >= 1
        assert section_patterns[0].condition["preference"] == "approve"

    def test_stores_in_db(self, env) -> None:
        """يحفظ الأنماط المُكتشَفة في قاعدة البيانات."""
        fc, pm = env["fc"], env["pm"]
        _seed_brand(fc, "StoredBrand", 10, action="approved")

        patterns = pm.discover_patterns(min_samples=5, min_confidence=0.7)
        assert len(patterns) > 0

        # تحقق من التخزين في DB
        stored_count = pm.count()
        assert stored_count > 0

        all_patterns = pm.get_all_patterns()
        assert len(all_patterns) == stored_count


# ---------------------------------------------------------------------------
# 3. استعلام الأنماط
# ---------------------------------------------------------------------------

class TestGetApplicablePatterns:
    def test_filters_by_brand(self, env) -> None:
        """يصفّي الأنماط حسب العلامة التجارية."""
        fc, pm = env["fc"], env["pm"]
        _seed_brand(fc, "Lattafa", 10, action="approved")
        _seed_brand(fc, "OtherBrand", 10, action="approved")

        pm.discover_patterns(min_samples=5, min_confidence=0.7)

        applicable = pm.get_applicable_patterns(brand="Lattafa")
        assert len(applicable) >= 1
        assert all(p.condition.get("brand") == "Lattafa" for p in applicable)

    def test_filters_by_section(self, env) -> None:
        """يصفّي الأنماط حسب القسم."""
        fc, pm = env["fc"], env["pm"]
        _seed_section(fc, "price_lower", 10, action="rejected")

        pm.discover_patterns(min_samples=5, min_confidence=0.7)

        applicable = pm.get_applicable_patterns(section="price_lower")
        assert len(applicable) >= 1
        assert all(p.condition.get("section") == "price_lower" for p in applicable)

    def test_sorted_by_confidence_desc(self, env) -> None:
        """يعيد الأنماط مرتّبة بالثقة تنازلياً."""
        pm = env["pm"]
        now = datetime.now(timezone.utc)

        # إدخال أنماط يدوية بثقات مختلفة
        for i, conf in enumerate([0.75, 0.95, 0.85]):
            p = UserPattern(
                pattern_id=f"manual_{i:04d}",
                pattern_type="brand_preference",
                description=f"نمط اختبار {i}",
                condition={"brand": f"Brand{i}"},
                confidence=conf,
                sample_count=10,
                approval_rate=0.9,
                avg_deviation=0.0,
                created_at=now,
                last_seen=now,
            )
            pm._upsert_pattern(p)

        applicable = pm.get_applicable_patterns(brand="Brand0")
        # يجب أن تكون Brand0 موجودة
        assert any(p.condition.get("brand") == "Brand0" for p in applicable)

        # تحقق من الترتيب العام
        all_p = pm.get_all_patterns()
        confs = [p.confidence for p in all_p]
        assert confs == sorted(confs, reverse=True)


# ---------------------------------------------------------------------------
# 4. get_all_patterns
# ---------------------------------------------------------------------------

class TestGetAllPatterns:
    def test_returns_all_stored(self, env) -> None:
        """يعيد كل الأنماط المخزّنة."""
        pm = env["pm"]
        now = datetime.now(timezone.utc)

        for i in range(5):
            p = UserPattern(
                pattern_id=f"all_test_{i:04d}",
                pattern_type="brand_preference",
                description=f"نمط {i}",
                condition={"brand": f"B{i}"},
                confidence=0.8 + i * 0.01,
                sample_count=10,
                approval_rate=0.9,
                avg_deviation=0.0,
                created_at=now,
                last_seen=now,
            )
            pm._upsert_pattern(p)

        all_p = pm.get_all_patterns()
        assert len(all_p) == 5

    def test_respects_limit(self, env) -> None:
        """يحترم الحد الأقصى لعدد الأنماط."""
        pm = env["pm"]
        now = datetime.now(timezone.utc)

        for i in range(10):
            p = UserPattern(
                pattern_id=f"lim_test_{i:04d}",
                pattern_type="brand_preference",
                description=f"نمط {i}",
                condition={"brand": f"B{i}"},
                confidence=0.8,
                sample_count=10,
                approval_rate=0.9,
                avg_deviation=0.0,
                created_at=now,
                last_seen=now,
            )
            pm._upsert_pattern(p)

        limited = pm.get_all_patterns(limit=3)
        assert len(limited) == 3


# ---------------------------------------------------------------------------
# 5. تنسيق للعرض
# ---------------------------------------------------------------------------

class TestFormatForPrompt:
    def test_returns_arabic_text(self, env) -> None:
        """ينسّق الأنماط كنص عربي."""
        pm = env["pm"]
        patterns = [
            UserPattern(
                pattern_id="fmt_001",
                pattern_type="brand_preference",
                description="المستخدم يوافق دائماً على أسعار Dior",
                condition={"brand": "Dior"},
                confidence=0.85,
                sample_count=12,
                approval_rate=0.9,
                avg_deviation=0.0,
            ),
        ]

        result = pm.format_for_prompt(patterns)
        assert "المستخدم يوافق دائماً على أسعار Dior" in result
        assert "ثقة 85%" in result
        assert "12 قرار" in result
        assert result.startswith("- ")

    def test_empty_list_returns_empty_string(self, env) -> None:
        """قائمة فارغة تعيد سلسلة فارغة."""
        pm = env["pm"]
        result = pm.format_for_prompt([])
        assert result == ""

    def test_multiple_patterns(self, env) -> None:
        """ينسّق عدة أنماط بشكل صحيح."""
        pm = env["pm"]
        patterns = [
            UserPattern(
                pattern_id="fmt_m1",
                pattern_type="brand_preference",
                description="نمط أول",
                condition={},
                confidence=0.9,
                sample_count=10,
            ),
            UserPattern(
                pattern_id="fmt_m2",
                pattern_type="section_bias",
                description="نمط ثاني",
                condition={},
                confidence=0.75,
                sample_count=8,
            ),
        ]

        result = pm.format_for_prompt(patterns)
        lines = result.strip().split("\n")
        assert len(lines) == 2
        assert all(line.startswith("- ") for line in lines)


# ---------------------------------------------------------------------------
# 6. حذف وإحصاء
# ---------------------------------------------------------------------------

class TestDeleteAndCount:
    def test_delete_pattern_removes(self, env) -> None:
        """يحذف نمطاً من قاعدة البيانات."""
        pm = env["pm"]
        now = datetime.now(timezone.utc)
        p = UserPattern(
            pattern_id="del_test_001",
            pattern_type="brand_preference",
            description="نمط للحذف",
            condition={"brand": "X"},
            confidence=0.9,
            sample_count=10,
            created_at=now,
            last_seen=now,
        )
        pm._upsert_pattern(p)
        assert pm.count() == 1

        result = pm.delete_pattern("del_test_001")
        assert result is True
        assert pm.count() == 0

    def test_delete_nonexistent_returns_false(self, env) -> None:
        """حذف نمط غير موجود يعيد False."""
        pm = env["pm"]
        result = pm.delete_pattern("nonexistent_id")
        assert result is False

    def test_count_returns_correct_number(self, env) -> None:
        """يعيد العدد الصحيح للأنماط المخزّنة."""
        pm = env["pm"]
        assert pm.count() == 0

        now = datetime.now(timezone.utc)
        for i in range(7):
            p = UserPattern(
                pattern_id=f"cnt_test_{i:04d}",
                pattern_type="brand_preference",
                description=f"نمط {i}",
                condition={"brand": f"B{i}"},
                confidence=0.8,
                sample_count=10,
                created_at=now,
                last_seen=now,
            )
            pm._upsert_pattern(p)

        assert pm.count() == 7


# ---------------------------------------------------------------------------
# 7. تحويل البيانات
# ---------------------------------------------------------------------------

class TestUserPatternSerialization:
    def test_to_dict_from_dict_roundtrip(self) -> None:
        """UserPattern.to_dict و from_dict يحافظان على البيانات."""
        now = datetime.now(timezone.utc)
        original = UserPattern(
            pattern_id="rt_test_001",
            pattern_type="brand_preference",
            description="المستخدم يوافق على Dior",
            condition={"brand": "Dior", "preference": "approve", "approval_rate": 0.92},
            confidence=0.92,
            sample_count=15,
            approval_rate=0.92,
            avg_deviation=3.5,
            created_at=now,
            last_seen=now,
        )

        d = original.to_dict()
        restored = UserPattern.from_dict(d)

        assert restored.pattern_id == original.pattern_id
        assert restored.pattern_type == original.pattern_type
        assert restored.description == original.description
        assert restored.condition == original.condition
        assert restored.confidence == original.confidence
        assert restored.sample_count == original.sample_count
        assert restored.approval_rate == original.approval_rate
        assert restored.avg_deviation == original.avg_deviation
        assert restored.created_at.isoformat() == now.isoformat()
        assert restored.last_seen.isoformat() == now.isoformat()

    def test_from_dict_with_json_string_condition(self) -> None:
        """from_dict يتعامل مع condition كسلسلة JSON."""
        d = {
            "pattern_id": "json_str_01",
            "pattern_type": "section_bias",
            "description": "وصف",
            "condition": json.dumps({"section": "price_raise"}),
            "confidence": 0.8,
            "sample_count": 10,
            "approval_rate": 0.9,
            "avg_deviation": 1.0,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_seen": datetime.now(timezone.utc).isoformat(),
        }
        p = UserPattern.from_dict(d)
        assert isinstance(p.condition, dict)
        assert p.condition["section"] == "price_raise"
