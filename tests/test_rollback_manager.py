"""tests/test_rollback_manager.py — اختبارات شاملة لمدير التراجع.

يستخدم قاعدة بيانات SQLite مؤقتة لكل اختبار عبر tempfile.
"""
from __future__ import annotations

import json
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from infrastructure.db_manager import DatabaseManager
from services.workflow.rollback_manager import RollbackManager, RollbackSnapshot


# ──────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────


@pytest.fixture()
def db(tmp_path: Path) -> DatabaseManager:
    """ينشئ مدير قاعدة بيانات مؤقت لكل اختبار."""
    db_path = str(tmp_path / "test_rollback.db")
    return DatabaseManager(db_path, wal=False)


@pytest.fixture()
def manager(db: DatabaseManager) -> RollbackManager:
    """ينشئ مدير تراجع مع جدول جاهز."""
    mgr = RollbackManager(db)
    mgr.ensure_table()
    return mgr


# بيانات اختبار مشتركة
BEFORE = {"price": 100.0, "name": "منتج أ"}
AFTER = {"price": 120.0, "name": "منتج أ"}
WEBHOOK = {"url": "https://hook.make.com/xxx", "status": 200}


# ──────────────────────────────────────────────────────────────
# الاختبارات
# ──────────────────────────────────────────────────────────────


class TestEnsureTable:
    """اختبارات إنشاء الجدول."""

    def test_ensure_table_creates_table(self, db: DatabaseManager) -> None:
        """يتحقّق أن ensure_table ينشئ جدول rollback_snapshots."""
        mgr = RollbackManager(db)
        assert not db.table_exists("rollback_snapshots")
        mgr.ensure_table()
        assert db.table_exists("rollback_snapshots")

    def test_ensure_table_idempotent(self, manager: RollbackManager, db: DatabaseManager) -> None:
        """يتحقّق أن استدعاء ensure_table مرتين لا يسبّب خطأ."""
        manager.ensure_table()  # مرة ثانية
        assert db.table_exists("rollback_snapshots")


class TestCreateSnapshot:
    """اختبارات إنشاء اللقطات."""

    def test_create_snapshot_stores_and_returns(self, manager: RollbackManager) -> None:
        """يتحقّق أن create_snapshot يُخزّن ويعيد لقطة صحيحة."""
        snap = manager.create_snapshot(
            entity_id="prod_001",
            action_type="price_update",
            before_state=BEFORE,
            after_state=AFTER,
            webhook_payload=WEBHOOK,
        )
        assert isinstance(snap, RollbackSnapshot)
        assert snap.entity_id == "prod_001"
        assert snap.action_type == "price_update"
        assert snap.before_state == BEFORE
        assert snap.after_state == AFTER
        assert snap.webhook_payload == WEBHOOK
        assert snap.is_reverted is False
        assert snap.reverted_at is None
        assert snap.batch_id is None
        assert len(snap.snapshot_id) == 12

    def test_create_snapshot_with_batch_id(self, manager: RollbackManager) -> None:
        """يتحقّق أن batch_id يُحفظ بشكل صحيح."""
        snap = manager.create_snapshot(
            entity_id="prod_002",
            action_type="new_product",
            before_state={},
            after_state=AFTER,
            batch_id="batch_abc",
        )
        assert snap.batch_id == "batch_abc"

        # يتحقّق من الاسترجاع من القاعدة
        fetched = manager.get_snapshot(snap.snapshot_id)
        assert fetched is not None
        assert fetched.batch_id == "batch_abc"

    def test_create_snapshot_default_webhook(self, manager: RollbackManager) -> None:
        """يتحقّق أن webhook_payload الافتراضي قاموس فارغ."""
        snap = manager.create_snapshot(
            entity_id="prod_003",
            action_type="redistribution",
            before_state=BEFORE,
            after_state=AFTER,
        )
        assert snap.webhook_payload == {}


class TestGetSnapshot:
    """اختبارات استرجاع لقطة واحدة."""

    def test_get_snapshot_returns_correct_data(self, manager: RollbackManager) -> None:
        """يتحقّق أن get_snapshot يعيد البيانات المخزّنة بدقّة."""
        snap = manager.create_snapshot(
            entity_id="prod_010",
            action_type="price_update",
            before_state=BEFORE,
            after_state=AFTER,
            webhook_payload=WEBHOOK,
        )
        fetched = manager.get_snapshot(snap.snapshot_id)
        assert fetched is not None
        assert fetched.snapshot_id == snap.snapshot_id
        assert fetched.entity_id == "prod_010"
        assert fetched.before_state == BEFORE
        assert fetched.after_state == AFTER
        assert fetched.webhook_payload == WEBHOOK

    def test_get_snapshot_returns_none_for_missing(self, manager: RollbackManager) -> None:
        """يتحقّق أن get_snapshot يعيد None لمعرّف غير موجود."""
        assert manager.get_snapshot("nonexistent_id") is None


class TestRollback:
    """اختبارات التراجع الفردي."""

    def test_rollback_marks_as_reverted(self, manager: RollbackManager) -> None:
        """يتحقّق أن rollback يُعلّم اللقطة كمُتراجع عنها مع طابع زمني."""
        snap = manager.create_snapshot(
            entity_id="prod_020",
            action_type="price_update",
            before_state=BEFORE,
            after_state=AFTER,
        )
        result = manager.rollback(snap.snapshot_id)
        assert result is True

        fetched = manager.get_snapshot(snap.snapshot_id)
        assert fetched is not None
        assert fetched.is_reverted is True
        assert fetched.reverted_at is not None
        assert isinstance(fetched.reverted_at, datetime)

    def test_rollback_returns_false_for_already_reverted(self, manager: RollbackManager) -> None:
        """يتحقّق أن rollback يعيد False إذا سبق التراجع."""
        snap = manager.create_snapshot(
            entity_id="prod_021",
            action_type="price_update",
            before_state=BEFORE,
            after_state=AFTER,
        )
        manager.rollback(snap.snapshot_id)
        result = manager.rollback(snap.snapshot_id)
        assert result is False

    def test_rollback_returns_false_for_missing(self, manager: RollbackManager) -> None:
        """يتحقّق أن rollback يعيد False لمعرّف غير موجود."""
        assert manager.rollback("missing_xyz") is False


class TestRollbackBatch:
    """اختبارات التراجع الجماعي."""

    def test_rollback_batch_reverts_all(self, manager: RollbackManager) -> None:
        """يتحقّق أن rollback_batch يتراجع عن كل لقطات الدفعة."""
        for i in range(3):
            manager.create_snapshot(
                entity_id=f"prod_03{i}",
                action_type="price_update",
                before_state=BEFORE,
                after_state=AFTER,
                batch_id="batch_001",
            )
        count = manager.rollback_batch("batch_001")
        assert count == 3

        # تأكيد أن جميعها مُتراجع عنها
        snapshots = manager.get_by_batch("batch_001")
        assert all(s.is_reverted for s in snapshots)

    def test_rollback_batch_returns_zero_for_empty(self, manager: RollbackManager) -> None:
        """يتحقّق أن rollback_batch يعيد 0 لدفعة غير موجودة."""
        assert manager.rollback_batch("nonexistent_batch") == 0

    def test_rollback_batch_skips_already_reverted(self, manager: RollbackManager) -> None:
        """يتحقّق أن rollback_batch يتجاوز اللقطات المُتراجع عنها سابقاً."""
        snaps = []
        for i in range(3):
            snaps.append(manager.create_snapshot(
                entity_id=f"prod_04{i}",
                action_type="price_update",
                before_state=BEFORE,
                after_state=AFTER,
                batch_id="batch_002",
            ))
        # تراجع عن الأولى يدوياً
        manager.rollback(snaps[0].snapshot_id)
        count = manager.rollback_batch("batch_002")
        assert count == 2  # فقط الاثنتان المتبقيتان


class TestGetHistory:
    """اختبارات سجل اللقطات."""

    def test_get_history_sorted_desc(self, manager: RollbackManager) -> None:
        """يتحقّق أن get_history يعيد النتائج مرتّبة تنازلياً حسب created_at."""
        for i in range(5):
            manager.create_snapshot(
                entity_id=f"prod_05{i}",
                action_type="price_update",
                before_state=BEFORE,
                after_state=AFTER,
            )
        history = manager.get_history(limit=10)
        assert len(history) == 5
        # التحقق من الترتيب التنازلي
        for i in range(len(history) - 1):
            assert history[i].created_at >= history[i + 1].created_at

    def test_get_history_respects_limit(self, manager: RollbackManager) -> None:
        """يتحقّق أن get_history يحترم الحدّ الأقصى."""
        for i in range(10):
            manager.create_snapshot(
                entity_id=f"prod_06{i}",
                action_type="price_update",
                before_state=BEFORE,
                after_state=AFTER,
            )
        history = manager.get_history(limit=3)
        assert len(history) == 3


class TestGetRevertable:
    """اختبارات اللقطات القابلة للتراجع."""

    def test_get_revertable_excludes_reverted(self, manager: RollbackManager) -> None:
        """يتحقّق أن get_revertable يستبعد اللقطات المُتراجع عنها."""
        snap1 = manager.create_snapshot(
            entity_id="prod_070",
            action_type="price_update",
            before_state=BEFORE,
            after_state=AFTER,
        )
        snap2 = manager.create_snapshot(
            entity_id="prod_071",
            action_type="new_product",
            before_state={},
            after_state=AFTER,
        )
        manager.rollback(snap1.snapshot_id)

        revertable = manager.get_revertable()
        assert len(revertable) == 1
        assert revertable[0].snapshot_id == snap2.snapshot_id


class TestGetByBatch:
    """اختبارات استعلام الدفعة."""

    def test_get_by_batch_returns_correct(self, manager: RollbackManager) -> None:
        """يتحقّق أن get_by_batch يعيد فقط لقطات الدفعة المحدّدة."""
        manager.create_snapshot(
            entity_id="prod_080",
            action_type="price_update",
            before_state=BEFORE,
            after_state=AFTER,
            batch_id="batch_A",
        )
        manager.create_snapshot(
            entity_id="prod_081",
            action_type="price_update",
            before_state=BEFORE,
            after_state=AFTER,
            batch_id="batch_B",
        )
        manager.create_snapshot(
            entity_id="prod_082",
            action_type="price_update",
            before_state=BEFORE,
            after_state=AFTER,
            batch_id="batch_A",
        )
        batch_a = manager.get_by_batch("batch_A")
        assert len(batch_a) == 2
        assert all(s.batch_id == "batch_A" for s in batch_a)

        batch_b = manager.get_by_batch("batch_B")
        assert len(batch_b) == 1


class TestIsReverted:
    """اختبارات فحص حالة التراجع."""

    def test_is_reverted_false_before_rollback(self, manager: RollbackManager) -> None:
        """يتحقّق أن is_reverted يعيد False قبل التراجع."""
        snap = manager.create_snapshot(
            entity_id="prod_090",
            action_type="price_update",
            before_state=BEFORE,
            after_state=AFTER,
        )
        assert manager.is_reverted(snap.snapshot_id) is False

    def test_is_reverted_true_after_rollback(self, manager: RollbackManager) -> None:
        """يتحقّق أن is_reverted يعيد True بعد التراجع."""
        snap = manager.create_snapshot(
            entity_id="prod_091",
            action_type="price_update",
            before_state=BEFORE,
            after_state=AFTER,
        )
        manager.rollback(snap.snapshot_id)
        assert manager.is_reverted(snap.snapshot_id) is True

    def test_is_reverted_false_for_missing(self, manager: RollbackManager) -> None:
        """يتحقّق أن is_reverted يعيد False لمعرّف غير موجود."""
        assert manager.is_reverted("ghost_id") is False


class TestSerialization:
    """اختبارات التسلسل وعكسه."""

    def test_json_roundtrip(self, manager: RollbackManager) -> None:
        """يتحقّق أن البيانات المعقّدة تُخزّن وتُسترجع بدقّة عبر JSON."""
        complex_before = {
            "price": 99.99,
            "tags": ["electronics", "sale"],
            "nested": {"key": "value", "count": 42},
            "arabic": "سعر المنتج",
        }
        complex_after = {
            "price": 120.50,
            "tags": ["electronics", "sale", "updated"],
            "nested": {"key": "new_value", "count": 43},
            "arabic": "سعر محدّث",
        }
        snap = manager.create_snapshot(
            entity_id="prod_100",
            action_type="price_update",
            before_state=complex_before,
            after_state=complex_after,
            webhook_payload={"sent": True, "code": 200},
        )
        fetched = manager.get_snapshot(snap.snapshot_id)
        assert fetched is not None
        assert fetched.before_state == complex_before
        assert fetched.after_state == complex_after
        assert fetched.webhook_payload == {"sent": True, "code": 200}

    def test_to_dict_from_dict_roundtrip(self) -> None:
        """يتحقّق أن to_dict و from_dict يحافظان على البيانات."""
        snap = RollbackSnapshot(
            snapshot_id="abc123def456",
            entity_id="prod_200",
            action_type="redistribution",
            before_state=BEFORE,
            after_state=AFTER,
            webhook_payload=WEBHOOK,
            created_at=datetime(2026, 6, 24, 0, 0, 0, tzinfo=timezone.utc),
            is_reverted=True,
            reverted_at=datetime(2026, 6, 24, 1, 0, 0, tzinfo=timezone.utc),
            batch_id="batch_xyz",
        )
        d = snap.to_dict()
        restored = RollbackSnapshot.from_dict(d)
        assert restored.snapshot_id == snap.snapshot_id
        assert restored.entity_id == snap.entity_id
        assert restored.action_type == snap.action_type
        assert restored.before_state == snap.before_state
        assert restored.after_state == snap.after_state
        assert restored.webhook_payload == snap.webhook_payload
        assert restored.is_reverted == snap.is_reverted
        assert restored.batch_id == snap.batch_id
        assert restored.reverted_at is not None
