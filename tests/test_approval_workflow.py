"""tests/test_approval_workflow.py — اختبارات شاملة لخدمة سير عمل الموافقات.

يستخدم قاعدة بيانات SQLite مؤقتة لكل اختبار عبر tmp_path.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from infrastructure.db_manager import DatabaseManager
from services.workflow.approval_workflow import (
    ApprovalRequest,
    ApprovalRule,
    ApprovalWorkflow,
)


# ──────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────


@pytest.fixture()
def db(tmp_path: Path) -> DatabaseManager:
    """ينشئ مدير قاعدة بيانات مؤقت لكل اختبار."""
    db_path = str(tmp_path / "test_approval.db")
    return DatabaseManager(db_path, wal=False)


@pytest.fixture()
def wf(db: DatabaseManager) -> ApprovalWorkflow:
    """ينشئ خدمة سير عمل الموافقات مع جدول جاهز."""
    service = ApprovalWorkflow(db)
    service.ensure_table()
    return service


# ──────────────────────────────────────────────────────────────
# 1. إنشاء الجدول
# ──────────────────────────────────────────────────────────────


class TestEnsureTable:
    """اختبارات إنشاء الجدول."""

    def test_ensure_table_creates_table(self, db: DatabaseManager) -> None:
        """يتحقّق أن ensure_table ينشئ جدول approval_requests."""
        service = ApprovalWorkflow(db)
        assert not db.table_exists("approval_requests")
        service.ensure_table()
        assert db.table_exists("approval_requests")

    def test_ensure_table_idempotent(self, wf: ApprovalWorkflow, db: DatabaseManager) -> None:
        """يتحقّق أن استدعاء ensure_table مرتين لا يسبّب خطأ."""
        wf.ensure_table()  # مرة ثانية
        assert db.table_exists("approval_requests")


# ──────────────────────────────────────────────────────────────
# 2. تقييم مستوى الموافقة
# ──────────────────────────────────────────────────────────────


class TestEvaluateApprovalLevel:
    """اختبارات تقييم مستوى الموافقة المطلوب."""

    def test_auto_approves_small_change(self, wf: ApprovalWorkflow) -> None:
        """تغيير سعري صغير (5%) يُعتمد تلقائياً."""
        req = wf.evaluate_approval_level(
            product_name="منتج اختبار",
            current_price=100.0,
            suggested_price=105.0,
            confidence=0.95,
        )
        assert req.status == "approved"
        assert req.required_level == "auto"
        assert req.resolved_by == "system"

    def test_requires_manager_for_15pct_change(self, wf: ApprovalWorkflow) -> None:
        """تغيير > 15% يتطلب موافقة مدير."""
        req = wf.evaluate_approval_level(
            product_name="منتج اختبار",
            current_price=100.0,
            suggested_price=120.0,
            confidence=0.9,
        )
        assert req.status == "pending"
        assert req.required_level == "manager"

    def test_requires_director_for_30pct_change(self, wf: ApprovalWorkflow) -> None:
        """تغيير > 30% يتطلب موافقة مدير تنفيذي."""
        req = wf.evaluate_approval_level(
            product_name="منتج اختبار",
            current_price=100.0,
            suggested_price=140.0,
            confidence=0.9,
        )
        assert req.status == "pending"
        assert req.required_level == "director"

    def test_requires_manager_for_low_confidence(self, wf: ApprovalWorkflow) -> None:
        """ثقة منخفضة (< 0.7) تتطلب موافقة مدير."""
        req = wf.evaluate_approval_level(
            product_name="منتج اختبار",
            current_price=100.0,
            suggested_price=102.0,
            confidence=0.5,
        )
        assert req.status == "pending"
        assert req.required_level == "manager"

    def test_requires_manager_for_large_amount(self, wf: ApprovalWorkflow) -> None:
        """مبلغ تغيير > 500 ريال يتطلب موافقة مدير."""
        req = wf.evaluate_approval_level(
            product_name="منتج غالي",
            current_price=5000.0,
            suggested_price=5600.0,
            confidence=0.95,
        )
        assert req.status == "pending"
        assert req.required_level in ("manager", "director")

    def test_requires_director_for_very_large_amount(self, wf: ApprovalWorkflow) -> None:
        """مبلغ تغيير > 1000 ريال يتطلب موافقة مدير تنفيذي."""
        req = wf.evaluate_approval_level(
            product_name="منتج باهظ",
            current_price=10000.0,
            suggested_price=11500.0,
            confidence=0.95,
        )
        assert req.status == "pending"
        assert req.required_level == "director"

    def test_evaluate_stores_in_db(self, wf: ApprovalWorkflow, db: DatabaseManager) -> None:
        """يتحقّق أن التقييم يخزّن الطلب في قاعدة البيانات."""
        req = wf.evaluate_approval_level(
            product_name="منتج مخزّن",
            current_price=100.0,
            suggested_price=125.0,
            confidence=0.85,
        )
        row = db.query_one(
            "SELECT * FROM approval_requests WHERE request_id = ?",
            (req.request_id,),
        )
        assert row is not None
        assert row["product_name"] == "منتج مخزّن"
        assert row["status"] == "pending"

    def test_triggered_rules_lists_which_rules_fired(self, wf: ApprovalWorkflow) -> None:
        """يتحقّق أن triggered_rules تحتوي على أسماء القواعد المُحقَّقة."""
        req = wf.evaluate_approval_level(
            product_name="منتج قواعد",
            current_price=100.0,
            suggested_price=140.0,
            confidence=0.5,
        )
        # يجب أن تتحقّق: تغيير سعر كبير (>15%)، تغيير سعر ضخم (>30%)، ثقة منخفضة
        assert len(req.triggered_rules) >= 3
        assert "تغيير سعر كبير" in req.triggered_rules
        assert "تغيير سعر ضخم" in req.triggered_rules
        assert "ثقة منخفضة" in req.triggered_rules

    def test_custom_rules_override_defaults(self, wf: ApprovalWorkflow) -> None:
        """يتحقّق أن القواعد المخصّصة تُستخدم بدلاً من الافتراضية."""
        custom = [
            ApprovalRule(
                rule_id="C001",
                name="قاعدة مخصّصة",
                description="قاعدة اختبار",
                condition="price_change_pct",
                threshold=5.0,
                operator="gt",
                required_level="director",
            ),
        ]
        req = wf.evaluate_approval_level(
            product_name="منتج مخصّص",
            current_price=100.0,
            suggested_price=110.0,
            confidence=0.95,
            custom_rules=custom,
        )
        assert req.required_level == "director"
        assert "قاعدة مخصّصة" in req.triggered_rules


# ──────────────────────────────────────────────────────────────
# 3. الموافقة والرفض
# ──────────────────────────────────────────────────────────────


class TestApproveReject:
    """اختبارات الموافقة والرفض."""

    def test_approve_changes_status(self, wf: ApprovalWorkflow) -> None:
        """يتحقّق أن approve يغيّر الحالة إلى approved."""
        req = wf.evaluate_approval_level(
            product_name="منتج للموافقة",
            current_price=100.0,
            suggested_price=120.0,
            confidence=0.9,
        )
        assert req.status == "pending"
        result = wf.approve(req.request_id, approved_by="أحمد", notes="موافق")
        assert result is True

        updated = wf.get_request(req.request_id)
        assert updated is not None
        assert updated.status == "approved"
        assert updated.resolved_by == "أحمد"
        assert updated.notes == "موافق"

    def test_reject_changes_status(self, wf: ApprovalWorkflow) -> None:
        """يتحقّق أن reject يغيّر الحالة إلى rejected."""
        req = wf.evaluate_approval_level(
            product_name="منتج للرفض",
            current_price=100.0,
            suggested_price=120.0,
            confidence=0.9,
        )
        result = wf.reject(req.request_id, rejected_by="سعد", notes="سعر غير مناسب")
        assert result is True

        updated = wf.get_request(req.request_id)
        assert updated is not None
        assert updated.status == "rejected"
        assert updated.resolved_by == "سعد"

    def test_approve_nonexistent_returns_false(self, wf: ApprovalWorkflow) -> None:
        """يتحقّق أن approve لطلب غير موجود يعيد False."""
        result = wf.approve("nonexistent_id")
        assert result is False

    def test_reject_nonexistent_returns_false(self, wf: ApprovalWorkflow) -> None:
        """يتحقّق أن reject لطلب غير موجود يعيد False."""
        result = wf.reject("nonexistent_id")
        assert result is False


# ──────────────────────────────────────────────────────────────
# 4. الاستعلامات
# ──────────────────────────────────────────────────────────────


class TestQueries:
    """اختبارات الاستعلام عن الطلبات."""

    def test_get_pending_returns_only_pending(self, wf: ApprovalWorkflow) -> None:
        """يتحقّق أن get_pending يعيد الطلبات المعلّقة فقط."""
        # طلب يُعتمد تلقائياً
        wf.evaluate_approval_level(
            product_name="صغير",
            current_price=100.0,
            suggested_price=102.0,
            confidence=0.95,
        )
        # طلب معلّق
        wf.evaluate_approval_level(
            product_name="كبير",
            current_price=100.0,
            suggested_price=120.0,
            confidence=0.9,
        )
        pending = wf.get_pending()
        assert len(pending) == 1
        assert pending[0].product_name == "كبير"

    def test_get_pending_filters_by_level(self, wf: ApprovalWorkflow) -> None:
        """يتحقّق أن get_pending يُصفّي حسب المستوى."""
        # مدير
        wf.evaluate_approval_level(
            product_name="متوسط",
            current_price=100.0,
            suggested_price=120.0,
            confidence=0.9,
        )
        # مدير تنفيذي
        wf.evaluate_approval_level(
            product_name="ضخم",
            current_price=100.0,
            suggested_price=140.0,
            confidence=0.9,
        )
        manager_pending = wf.get_pending(level="manager")
        director_pending = wf.get_pending(level="director")
        assert len(manager_pending) == 1
        assert manager_pending[0].product_name == "متوسط"
        assert len(director_pending) == 1
        assert director_pending[0].product_name == "ضخم"

    def test_get_request_returns_correct_data(self, wf: ApprovalWorkflow) -> None:
        """يتحقّق أن get_request يعيد البيانات الصحيحة."""
        req = wf.evaluate_approval_level(
            product_name="منتج استعلام",
            brand="ماركة",
            current_price=200.0,
            suggested_price=250.0,
            confidence=0.85,
        )
        fetched = wf.get_request(req.request_id)
        assert fetched is not None
        assert fetched.request_id == req.request_id
        assert fetched.product_name == "منتج استعلام"
        assert fetched.brand == "ماركة"
        assert fetched.current_price == 200.0
        assert fetched.suggested_price == 250.0

    def test_get_request_returns_none_for_missing(self, wf: ApprovalWorkflow) -> None:
        """يتحقّق أن get_request يعيد None لمعرّف غير موجود."""
        assert wf.get_request("missing_id") is None

    def test_get_stats_returns_correct_counts(self, wf: ApprovalWorkflow) -> None:
        """يتحقّق أن get_stats يعيد الأعداد الصحيحة."""
        # إنشاء طلبات بحالات مختلفة
        r1 = wf.evaluate_approval_level(
            product_name="معلّق1",
            current_price=100.0,
            suggested_price=120.0,
            confidence=0.9,
        )
        r2 = wf.evaluate_approval_level(
            product_name="معلّق2",
            current_price=100.0,
            suggested_price=125.0,
            confidence=0.9,
        )
        # موافقة تلقائية
        wf.evaluate_approval_level(
            product_name="آلي",
            current_price=100.0,
            suggested_price=102.0,
            confidence=0.95,
        )
        # موافقة يدوية
        wf.approve(r1.request_id)
        # رفض
        wf.reject(r2.request_id)

        stats = wf.get_stats()
        assert stats["pending"] == 0
        assert stats["approved"] == 2  # آلي + يدوي
        assert stats["rejected"] == 1
        assert isinstance(stats["avg_resolution_time_hours"], float)


# ──────────────────────────────────────────────────────────────
# 5. انتهاء الصلاحية
# ──────────────────────────────────────────────────────────────


class TestExpireOld:
    """اختبارات انتهاء صلاحية الطلبات."""

    def test_expire_old_marks_old_requests(self, wf: ApprovalWorkflow, db: DatabaseManager) -> None:
        """يتحقّق أن expire_old ينهي صلاحية الطلبات القديمة."""
        # إنشاء طلب معلّق
        req = wf.evaluate_approval_level(
            product_name="قديم",
            current_price=100.0,
            suggested_price=120.0,
            confidence=0.9,
        )
        # تحديث created_at لجعله قديماً (3 أيام)
        old_time = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
        db.execute(
            "UPDATE approval_requests SET created_at = ? WHERE request_id = ?",
            (old_time, req.request_id),
        )
        expired_count = wf.expire_old(hours=48)
        assert expired_count == 1

        updated = wf.get_request(req.request_id)
        assert updated is not None
        assert updated.status == "expired"

    def test_expire_old_does_not_affect_recent(self, wf: ApprovalWorkflow) -> None:
        """يتحقّق أن expire_old لا يؤثر على الطلبات الحديثة."""
        wf.evaluate_approval_level(
            product_name="حديث",
            current_price=100.0,
            suggested_price=120.0,
            confidence=0.9,
        )
        expired_count = wf.expire_old(hours=48)
        assert expired_count == 0


# ──────────────────────────────────────────────────────────────
# 6. to_dict roundtrip
# ──────────────────────────────────────────────────────────────


class TestToDict:
    """اختبارات تحويل ApprovalRequest إلى قاموس."""

    def test_to_dict_roundtrip(self, wf: ApprovalWorkflow) -> None:
        """يتحقّق أن to_dict يُنتج قاموساً قابلاً للتسلسل كـ JSON."""
        req = wf.evaluate_approval_level(
            product_name="منتج قاموس",
            brand="ماركة ق",
            current_price=300.0,
            suggested_price=350.0,
            confidence=0.8,
        )
        d = req.to_dict()

        # يجب أن يكون قابلاً للتسلسل
        json_str = json.dumps(d, ensure_ascii=False)
        parsed = json.loads(json_str)

        assert parsed["request_id"] == req.request_id
        assert parsed["product_name"] == "منتج قاموس"
        assert parsed["brand"] == "ماركة ق"
        assert parsed["current_price"] == 300.0
        assert parsed["suggested_price"] == 350.0
        assert isinstance(parsed["triggered_rules"], list)
        assert parsed["created_at"] is not None
