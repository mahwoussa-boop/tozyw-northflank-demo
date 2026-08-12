"""tests/test_product_repository.py — اختبارات شاملة لمستودع كيان المنتج.

يغطي 14 اختبار:
- إنشاء الجداول
- إدراج + استرجاع (roundtrip)
- إدراج دفعي (batch)
- البحث بمعرّف المنتج
- التصفية بالحالة
- المنتجات القديمة (stale)
- الخط الزمني مرتّب
- تسجيل الأحداث
- عدّ المنتجات بالحالة
- تحديث كيان موجود (لا يتكرر)
- إرجاع None لكيان غير موجود
- تسلسل JSON لأسعار المنافسين (roundtrip)
- upsert_batch بقائمة فارغة
- get يحمّل الأحداث
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from core.product_entity import (
    EventType,
    ProductEntity,
    ProductEvent,
    ProductState,
)
from infrastructure.db_manager import DatabaseManager
from infrastructure.product_repository import ProductRepository


# ════════════════════════════════════════════════════════════════════
#  Fixtures
# ════════════════════════════════════════════════════════════════════


@pytest.fixture()
def repo(tmp_path):
    """يُهيّئ مستودع بقاعدة بيانات مؤقتة مع الجداول جاهزة."""
    db_path = str(tmp_path / "test.db")
    db = DatabaseManager(db_path)
    repo = ProductRepository(db)
    repo.ensure_tables()
    yield repo
    db.close()


def _make_entity(
    entity_id: str = "ent001",
    product_id: str = "P100",
    name: str = "منتج تجريبي",
    state: ProductState = ProductState.DISCOVERED,
    current_price: float = 25.0,
    competitor_prices: dict | None = None,
    updated_at: datetime | None = None,
) -> ProductEntity:
    """ينشئ كيان اختباري بقيم افتراضية مناسبة."""
    now = datetime.now(timezone.utc)
    return ProductEntity(
        entity_id=entity_id,
        product_id=product_id,
        name=name,
        state=state,
        current_price=current_price,
        competitor_prices=competitor_prices or {},
        created_at=now,
        updated_at=updated_at or now,
    )


# ════════════════════════════════════════════════════════════════════
#  الاختبارات
# ════════════════════════════════════════════════════════════════════


class TestEnsureTables:
    """اختبارات إنشاء الجداول."""

    def test_creates_tables(self, repo: ProductRepository):
        """ensure_tables يُنشئ product_entities و product_events."""
        assert repo._db.table_exists("product_entities")
        assert repo._db.table_exists("product_events")

    def test_idempotent(self, repo: ProductRepository):
        """ensure_tables آمن للتكرار — لا يرفع خطأ عند التشغيل مرتين."""
        repo.ensure_tables()  # مرة ثانية
        assert repo._db.table_exists("product_entities")


class TestUpsertAndGet:
    """اختبارات الإدراج والاسترجاع."""

    def test_upsert_get_roundtrip(self, repo: ProductRepository):
        """upsert ثم get يعيد نفس البيانات."""
        entity = _make_entity()
        returned_id = repo.upsert(entity)

        assert returned_id == "ent001"

        fetched = repo.get("ent001")
        assert fetched is not None
        assert fetched.entity_id == "ent001"
        assert fetched.product_id == "P100"
        assert fetched.name == "منتج تجريبي"
        assert fetched.state == ProductState.DISCOVERED
        assert fetched.current_price == 25.0

    def test_upsert_updates_existing(self, repo: ProductRepository):
        """upsert على entity_id موجود يحدّث بدل أن يُكرر."""
        entity1 = _make_entity(current_price=25.0)
        repo.upsert(entity1)

        entity2 = _make_entity(current_price=30.0)
        repo.upsert(entity2)

        # يجب أن يكون صف واحد فقط
        rows = repo._db.query(
            "SELECT COUNT(*) as cnt FROM product_entities WHERE entity_id = ?",
            ("ent001",),
        )
        assert rows[0]["cnt"] == 1

        fetched = repo.get("ent001")
        assert fetched is not None
        assert fetched.current_price == 30.0

    def test_get_returns_none_for_missing(self, repo: ProductRepository):
        """get يعيد None لمعرّف غير موجود."""
        result = repo.get("nonexistent_id")
        assert result is None


class TestCompetitorPricesJson:
    """اختبارات تسلسل JSON لأسعار المنافسين."""

    def test_competitor_prices_roundtrip(self, repo: ProductRepository):
        """competitor_prices يُخزَّن كـ JSON ويُسترجع كقاموس."""
        prices = {"متجر_أ": 20.0, "متجر_ب": 22.5, "store_c": 19.99}
        entity = _make_entity(competitor_prices=prices)
        repo.upsert(entity)

        fetched = repo.get("ent001")
        assert fetched is not None
        assert fetched.competitor_prices == prices
        assert isinstance(fetched.competitor_prices, dict)
        assert fetched.competitor_prices["متجر_أ"] == 20.0


class TestFindByProductId:
    """اختبارات البحث بمعرّف المنتج."""

    def test_find_by_product_id(self, repo: ProductRepository):
        """find_by_product_id يعيد كل الكيانات بنفس product_id."""
        repo.upsert(_make_entity(entity_id="e1", product_id="P200"))
        repo.upsert(_make_entity(entity_id="e2", product_id="P200"))
        repo.upsert(_make_entity(entity_id="e3", product_id="P300"))

        results = repo.find_by_product_id("P200")
        assert len(results) == 2
        assert all(e.product_id == "P200" for e in results)

    def test_find_by_product_id_empty(self, repo: ProductRepository):
        """find_by_product_id يعيد قائمة فارغة لمنتج غير موجود."""
        results = repo.find_by_product_id("UNKNOWN")
        assert results == []


class TestGetByState:
    """اختبارات التصفية بالحالة."""

    def test_get_by_state(self, repo: ProductRepository):
        """get_by_state يعيد الكيانات بالحالة المطلوبة فقط."""
        repo.upsert(_make_entity(entity_id="e1", state=ProductState.DISCOVERED))
        repo.upsert(_make_entity(entity_id="e2", state=ProductState.PRICED))
        repo.upsert(_make_entity(entity_id="e3", state=ProductState.DISCOVERED))

        discovered = repo.get_by_state(ProductState.DISCOVERED)
        assert len(discovered) == 2
        assert all(e.state == ProductState.DISCOVERED for e in discovered)

        priced = repo.get_by_state(ProductState.PRICED)
        assert len(priced) == 1
        assert priced[0].state == ProductState.PRICED


class TestGetStale:
    """اختبارات المنتجات القديمة."""

    def test_get_stale(self, repo: ProductRepository):
        """get_stale يعيد منتجات لم تُحدَّث منذ N يوم."""
        old_date = datetime.now(timezone.utc) - timedelta(days=10)
        recent_date = datetime.now(timezone.utc)

        repo.upsert(_make_entity(entity_id="old1", updated_at=old_date))
        repo.upsert(_make_entity(entity_id="recent1", updated_at=recent_date))

        stale = repo.get_stale(days=7)
        assert len(stale) == 1
        assert stale[0].entity_id == "old1"


class TestEvents:
    """اختبارات الأحداث والخط الزمني."""

    def test_record_event_stores_event(self, repo: ProductRepository):
        """record_event يخزّن الحدث في قاعدة البيانات."""
        repo.upsert(_make_entity(entity_id="e1"))

        event = ProductEvent(
            event_id="ev001",
            event_type=EventType.STATE_CHANGE,
            old_state="discovered",
            new_state="matched",
            actor="test",
        )
        repo.record_event("e1", event)

        rows = repo._db.query(
            "SELECT * FROM product_events WHERE entity_id = ?", ("e1",)
        )
        assert len(rows) == 1
        assert rows[0]["event_id"] == "ev001"
        assert rows[0]["event_type"] == "state_change"

    def test_get_timeline_sorted(self, repo: ProductRepository):
        """get_timeline يعيد الأحداث مرتّبة بالتوقيت تصاعدياً."""
        repo.upsert(_make_entity(entity_id="e1"))

        now = datetime.now(timezone.utc)
        events = [
            ProductEvent(
                event_id=f"ev{i}",
                timestamp=now + timedelta(hours=i),
                event_type=EventType.STATE_CHANGE,
            )
            for i in [3, 1, 2]  # ترتيب عشوائي عمداً
        ]
        for ev in events:
            repo.record_event("e1", ev)

        timeline = repo.get_timeline("e1")
        assert len(timeline) == 3
        # التحقق أنها مرتّبة تصاعدياً
        timestamps = [e.timestamp for e in timeline]
        assert timestamps == sorted(timestamps)

    def test_get_loads_events(self, repo: ProductRepository):
        """get() يحمّل الأحداث في entity.events."""
        repo.upsert(_make_entity(entity_id="e1"))

        event = ProductEvent(
            event_id="ev100",
            event_type=EventType.PRICE_UPDATE,
            actor="test",
            data={"old_price": 10, "new_price": 15},
        )
        repo.record_event("e1", event)

        fetched = repo.get("e1")
        assert fetched is not None
        assert len(fetched.events) == 1
        assert fetched.events[0].event_id == "ev100"
        assert fetched.events[0].data["new_price"] == 15


class TestCountByState:
    """اختبارات العدّ بالحالة."""

    def test_count_by_state(self, repo: ProductRepository):
        """count_by_state يعيد عدد الكيانات لكل حالة."""
        repo.upsert(_make_entity(entity_id="e1", state=ProductState.DISCOVERED))
        repo.upsert(_make_entity(entity_id="e2", state=ProductState.DISCOVERED))
        repo.upsert(_make_entity(entity_id="e3", state=ProductState.PRICED))
        repo.upsert(_make_entity(entity_id="e4", state=ProductState.SENT))

        counts = repo.count_by_state()
        assert counts["discovered"] == 2
        assert counts["priced"] == 1
        assert counts["sent"] == 1
        assert len(counts) == 3


class TestUpsertBatch:
    """اختبارات الإدراج الدفعي."""

    def test_upsert_batch(self, repo: ProductRepository):
        """upsert_batch يدرج عدة كيانات ويعيد العدد الصحيح."""
        entities = [
            _make_entity(entity_id=f"batch_{i}", product_id=f"BP{i}")
            for i in range(5)
        ]
        count = repo.upsert_batch(entities)
        assert count == 5

        # التحقق أنها كلها مخزّنة
        for i in range(5):
            fetched = repo.get(f"batch_{i}")
            assert fetched is not None
            assert fetched.product_id == f"BP{i}"

    def test_upsert_batch_empty(self, repo: ProductRepository):
        """upsert_batch بقائمة فارغة يعيد 0."""
        count = repo.upsert_batch([])
        assert count == 0
