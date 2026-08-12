"""tests/test_autopilot.py — اختبارات شاملة لخدمة AutoPilotEngine.

يغطّي التقييم بجميع القواعد، تسجيل الإجراءات، الإحصائيات اليومية،
وتحديث الإعدادات.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from infrastructure.db_manager import DatabaseManager
from services.workflow.autopilot import (
    AutoPilotConfig,
    AutoPilotEngine,
    AutoPilotReport,
    AutoPilotVerdict,
)
from services.workflow.rollback_manager import RollbackManager


# ──────────────────────────────────────────────────────────────
# تثبيتات (fixtures)
# ──────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path: Path) -> DatabaseManager:
    """ينشئ قاعدة بيانات مؤقتة في tmp_path."""
    db = DatabaseManager(str(tmp_path / "test_autopilot.db"))
    return db


@pytest.fixture
def rollback(tmp_db: DatabaseManager) -> RollbackManager:
    """ينشئ مدير تراجع مع ضمان جدوله."""
    rm = RollbackManager(tmp_db)
    rm.ensure_table()
    return rm


@pytest.fixture
def engine(tmp_db: DatabaseManager, rollback: RollbackManager) -> AutoPilotEngine:
    """ينشئ محرّك طيار آلي مُفعَّل وجاهز."""
    config = AutoPilotConfig(
        enabled=True,
        min_confidence=0.90,
        max_price_change_pct=10.0,
        excluded_brands=["Gucci"],
        max_daily_actions=100,
        require_consensus=True,
    )
    eng = AutoPilotEngine(config=config, rollback=rollback, db=tmp_db)
    eng.ensure_table()
    return eng


@pytest.fixture
def disabled_engine(tmp_db: DatabaseManager, rollback: RollbackManager) -> AutoPilotEngine:
    """ينشئ محرّك طيار آلي مُعطَّل."""
    config = AutoPilotConfig(enabled=False)
    eng = AutoPilotEngine(config=config, rollback=rollback, db=tmp_db)
    eng.ensure_table()
    return eng


# ──────────────────────────────────────────────────────────────
# اختبارات AutoPilotVerdict
# ──────────────────────────────────────────────────────────────

class TestAutoPilotVerdict:
    """اختبارات إنشاء كائن AutoPilotVerdict."""

    def test_creation(self):
        """يتحقّق أن الكائن يُنشأ بالقيم المُمرَّرة."""
        v = AutoPilotVerdict(
            can_execute=True,
            reason="تمّ التنفيذ",
            approval_level="auto",
        )
        assert v.can_execute is True
        assert v.approval_level == "auto"
        assert v.blocked_by == []

    def test_creation_with_blocked(self):
        """يتحقّق من الإنشاء مع قائمة أسباب حظر."""
        v = AutoPilotVerdict(
            can_execute=False,
            reason="محظور",
            approval_level="manual",
            blocked_by=["low_confidence", "excluded_brand"],
        )
        assert not v.can_execute
        assert len(v.blocked_by) == 2


class TestAutoPilotReport:
    """اختبارات إنشاء كائن AutoPilotReport."""

    def test_creation_defaults(self):
        """يتحقّق من القيم الافتراضية."""
        r = AutoPilotReport()
        assert r.total_evaluated == 0
        assert r.details == []


# ──────────────────────────────────────────────────────────────
# اختبارات التقييم (evaluate)
# ──────────────────────────────────────────────────────────────

class TestEvaluate:
    """اختبارات evaluate مع جميع قواعد الأمان."""

    def test_safe_case_can_execute(self, engine: AutoPilotEngine):
        """الحالة الآمنة — جميع القواعد مُستوفاة → can_execute = True."""
        v = engine.evaluate(
            product_name="TestProduct",
            brand="Nike",
            current_price=100.0,
            suggested_price=105.0,
            confidence=0.95,
            consensus_type="unanimous",
        )
        assert v.can_execute is True
        assert v.approval_level == "auto"
        assert v.blocked_by == []

    def test_blocks_when_disabled(self, disabled_engine: AutoPilotEngine):
        """يحظر التنفيذ عندما يكون الطيار الآلي مُعطَّلاً."""
        v = disabled_engine.evaluate(
            product_name="TestProduct",
            current_price=100.0,
            suggested_price=105.0,
            confidence=0.95,
        )
        assert v.can_execute is False
        assert "autopilot_disabled" in v.blocked_by

    def test_blocks_low_confidence(self, engine: AutoPilotEngine):
        """يحظر التنفيذ عندما تكون الثقة منخفضة."""
        v = engine.evaluate(
            product_name="TestProduct",
            current_price=100.0,
            suggested_price=105.0,
            confidence=0.50,
        )
        assert v.can_execute is False
        assert "low_confidence" in v.blocked_by

    def test_blocks_large_price_change(self, engine: AutoPilotEngine):
        """يحظر التنفيذ عندما يتجاوز تغيير السعر الحدّ الأقصى."""
        v = engine.evaluate(
            product_name="TestProduct",
            current_price=100.0,
            suggested_price=200.0,  # 100% change
            confidence=0.95,
        )
        assert v.can_execute is False
        assert "price_change_too_large" in v.blocked_by

    def test_blocks_excluded_brand(self, engine: AutoPilotEngine):
        """يحظر التنفيذ للعلامات التجارية المستبعدة."""
        v = engine.evaluate(
            product_name="TestProduct",
            brand="Gucci",
            current_price=100.0,
            suggested_price=105.0,
            confidence=0.95,
        )
        assert v.can_execute is False
        assert "excluded_brand" in v.blocked_by

    def test_blocks_daily_limit_exceeded(
        self, tmp_db: DatabaseManager, rollback: RollbackManager
    ):
        """يحظر التنفيذ عند تجاوز الحدّ اليومي."""
        config = AutoPilotConfig(
            enabled=True,
            min_confidence=0.90,
            max_daily_actions=2,
            require_consensus=False,
        )
        eng = AutoPilotEngine(config=config, rollback=rollback, db=tmp_db)
        eng.ensure_table()

        # تسجيل إجراءَين لتجاوز الحدّ
        eng.record_action("P1", "auto_executed", price=100.0)
        eng.record_action("P2", "auto_executed", price=200.0)

        v = eng.evaluate(
            product_name="P3",
            current_price=100.0,
            suggested_price=105.0,
            confidence=0.95,
        )
        assert v.can_execute is False
        assert "daily_limit_exceeded" in v.blocked_by

    def test_blocks_non_unanimous(self, engine: AutoPilotEngine):
        """يحظر التنفيذ عند عدم وجود إجماع كامل."""
        v = engine.evaluate(
            product_name="TestProduct",
            current_price=100.0,
            suggested_price=105.0,
            confidence=0.95,
            consensus_type="majority",
        )
        assert v.can_execute is False
        assert "no_consensus" in v.blocked_by

    def test_returns_all_blocked_by_reasons(self, disabled_engine: AutoPilotEngine):
        """يعيد جميع أسباب الحظر عند فشل عدة قواعد."""
        v = disabled_engine.evaluate(
            product_name="TestProduct",
            brand="Gucci",
            current_price=100.0,
            suggested_price=200.0,
            confidence=0.50,
            consensus_type="majority",
        )
        assert v.can_execute is False
        # يجب أن تحتوي blocked_by على عدة أسباب
        assert "autopilot_disabled" in v.blocked_by
        assert "low_confidence" in v.blocked_by
        assert "price_change_too_large" in v.blocked_by
        assert len(v.blocked_by) >= 3

    def test_approval_level_review(self, engine: AutoPilotEngine):
        """مستوى المراجعة review عند حظر غير شديد (مثل low_confidence)."""
        v = engine.evaluate(
            product_name="TestProduct",
            current_price=100.0,
            suggested_price=105.0,
            confidence=0.80,
        )
        assert v.approval_level == "review"

    def test_approval_level_manual(self, engine: AutoPilotEngine):
        """مستوى manual عند حظر شديد (مثل excluded_brand)."""
        v = engine.evaluate(
            product_name="TestProduct",
            brand="Gucci",
            current_price=100.0,
            suggested_price=105.0,
            confidence=0.95,
        )
        assert v.approval_level == "manual"


# ──────────────────────────────────────────────────────────────
# اختبارات تسجيل الإجراءات
# ──────────────────────────────────────────────────────────────

class TestRecordAction:
    """اختبارات record_action."""

    def test_stores_in_db(self, engine: AutoPilotEngine, tmp_db: DatabaseManager):
        """يتحقّق أن الإجراء يُخزَّن في قاعدة البيانات."""
        engine.record_action("ProductX", "auto_executed", price=55.0, details={"note": "ok"})
        rows = tmp_db.query("SELECT * FROM autopilot_log WHERE product_name = ?", ("ProductX",))
        assert len(rows) == 1
        row = rows[0]
        assert row["action"] == "auto_executed"
        assert row["price"] == 55.0
        details = json.loads(row["details"])
        assert details["note"] == "ok"

    def test_stores_without_details(self, engine: AutoPilotEngine, tmp_db: DatabaseManager):
        """يتحقّق من التخزين بدون تفاصيل (افتراضي {})."""
        engine.record_action("ProductY", "blocked")
        rows = tmp_db.query("SELECT * FROM autopilot_log WHERE product_name = ?", ("ProductY",))
        assert len(rows) == 1
        assert json.loads(rows[0]["details"]) == {}


# ──────────────────────────────────────────────────────────────
# اختبارات الإحصائيات اليومية
# ──────────────────────────────────────────────────────────────

class TestDailyStats:
    """اختبارات get_daily_count و get_daily_stats."""

    def test_daily_count_correct(self, engine: AutoPilotEngine):
        """يعيد العدد الصحيح لإجراءات auto_executed اليوم."""
        engine.record_action("P1", "auto_executed", price=10.0)
        engine.record_action("P2", "auto_executed", price=20.0)
        engine.record_action("P3", "blocked", price=30.0)
        assert engine.get_daily_count() == 2

    def test_daily_count_zero_initially(self, engine: AutoPilotEngine):
        """العدد صفر في البداية."""
        assert engine.get_daily_count() == 0

    def test_daily_stats_complete(self, engine: AutoPilotEngine):
        """يعيد إحصائيات كاملة."""
        engine.record_action("P1", "auto_executed", price=10.0)
        engine.record_action("P2", "sent_to_review", price=20.0)
        engine.record_action("P3", "blocked", price=30.0)
        engine.record_action("P4", "auto_executed", price=40.0)

        stats = engine.get_daily_stats()
        assert stats["auto_executed"] == 2
        assert stats["reviewed"] == 1
        assert stats["blocked"] == 1
        assert stats["remaining_budget"] == 100 - 2

    def test_remaining_budget_calculation(self, engine: AutoPilotEngine):
        """يتحقّق من حساب الميزانية المتبقية."""
        stats = engine.get_daily_stats()
        assert stats["remaining_budget"] == 100


# ──────────────────────────────────────────────────────────────
# اختبارات تحديث الإعدادات
# ──────────────────────────────────────────────────────────────

class TestUpdateConfig:
    """اختبارات update_config."""

    def test_updates_correctly(self, engine: AutoPilotEngine):
        """يتحقّق من تحديث الإعدادات بنجاح."""
        new_config = engine.update_config(
            min_confidence=0.80,
            max_daily_actions=200,
        )
        assert new_config.min_confidence == 0.80
        assert new_config.max_daily_actions == 200
        # باقي الإعدادات لم تتغيّر
        assert new_config.enabled is True
        assert new_config.max_price_change_pct == 10.0

    def test_update_enabled_toggle(self, engine: AutoPilotEngine):
        """يتحقّق من تعطيل/تفعيل الطيار الآلي."""
        engine.update_config(enabled=False)
        v = engine.evaluate(
            product_name="Test",
            current_price=100.0,
            suggested_price=105.0,
            confidence=0.95,
        )
        assert v.can_execute is False
        assert "autopilot_disabled" in v.blocked_by

    def test_update_invalid_field_raises(self, engine: AutoPilotEngine):
        """يرفع خطأ عند تحديث حقل غير موجود."""
        with pytest.raises(AttributeError):
            engine.update_config(nonexistent_field=42)

    def test_update_excluded_brands(self, engine: AutoPilotEngine):
        """يتحقّق من تحديث قائمة العلامات المستبعدة."""
        engine.update_config(excluded_brands=["Nike", "Adidas"])
        v = engine.evaluate(
            product_name="NikeShoe",
            brand="Nike",
            current_price=100.0,
            suggested_price=105.0,
            confidence=0.95,
        )
        assert not v.can_execute
        assert "excluded_brand" in v.blocked_by


# ──────────────────────────────────────────────────────────────
# اختبار ensure_table
# ──────────────────────────────────────────────────────────────

class TestEnsureTable:
    """اختبارات ensure_table."""

    def test_creates_table(self, tmp_db: DatabaseManager, rollback: RollbackManager):
        """يتحقّق أن الجدول يُنشأ بنجاح."""
        config = AutoPilotConfig()
        eng = AutoPilotEngine(config=config, rollback=rollback, db=tmp_db)
        eng.ensure_table()
        assert tmp_db.table_exists("autopilot_log")

    def test_idempotent(self, engine: AutoPilotEngine, tmp_db: DatabaseManager):
        """استدعاء ensure_table مرتين لا يسبّب خطأ."""
        engine.ensure_table()
        engine.ensure_table()
        assert tmp_db.table_exists("autopilot_log")
