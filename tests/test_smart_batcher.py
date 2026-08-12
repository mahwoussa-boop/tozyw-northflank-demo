"""tests/test_smart_batcher.py — اختبارات شاملة لخدمة SmartBatcher.

يغطّي التجميع حسب العلامة التجارية والأولوية والقسم، وتقسيم الدُّفعات.
"""
from __future__ import annotations

import pytest

from services.workflow.smart_batcher import BatchGroup, SmartBatcher


# ──────────────────────────────────────────────────────────────
# تثبيتات (fixtures)
# ──────────────────────────────────────────────────────────────

@pytest.fixture
def batcher() -> SmartBatcher:
    """يعيد مثيل SmartBatcher جديد."""
    return SmartBatcher()


@pytest.fixture
def sample_items() -> list[dict]:
    """عناصر تسعير عيّنة متنوّعة."""
    return [
        {"name": "A", "brand": "Nike", "section": "price_raise", "confidence": 0.95, "current_price": 100, "suggested_price": 110},
        {"name": "B", "brand": "Nike", "section": "price_raise", "confidence": 0.85, "current_price": 200, "suggested_price": 230},
        {"name": "C", "brand": "Adidas", "section": "price_lower", "confidence": 0.60, "current_price": 150, "suggested_price": 140},
        {"name": "D", "brand": "Adidas", "section": "price_lower", "confidence": 0.92, "current_price": 300, "suggested_price": 250},
        {"name": "E", "brand": "Puma", "section": "price_raise", "confidence": 0.75, "current_price": 80, "suggested_price": 85},
    ]


# ──────────────────────────────────────────────────────────────
# اختبارات BatchGroup
# ──────────────────────────────────────────────────────────────

class TestBatchGroup:
    """اختبارات إنشاء كائن BatchGroup."""

    def test_creation_defaults(self):
        """يتحقّق أن القيم الافتراضية تُضبط بشكل صحيح."""
        bg = BatchGroup(group_id="abc123", group_key="Nike", group_type="brand")
        assert bg.group_id == "abc123"
        assert bg.group_key == "Nike"
        assert bg.group_type == "brand"
        assert bg.items == []
        assert bg.total_price_delta == 0.0
        assert bg.avg_confidence == 0.0

    def test_creation_with_items(self):
        """يتحقّق أن الكائن يُنشأ مع عناصر مخصّصة."""
        items = [{"name": "X"}]
        bg = BatchGroup(
            group_id="def456",
            group_key="urgent",
            group_type="priority",
            items=items,
            total_price_delta=50.0,
            avg_confidence=0.85,
        )
        assert bg.items == [{"name": "X"}]
        assert bg.total_price_delta == 50.0
        assert bg.avg_confidence == 0.85


# ──────────────────────────────────────────────────────────────
# اختبارات التجميع حسب العلامة التجارية
# ──────────────────────────────────────────────────────────────

class TestGroupByBrand:
    """اختبارات group_by_brand."""

    def test_groups_correctly(self, batcher: SmartBatcher, sample_items: list[dict]):
        """يتحقّق أن العناصر تُجمَّع حسب العلامة التجارية."""
        groups = batcher.group_by_brand(sample_items)
        brand_keys = {g.group_key for g in groups}
        assert brand_keys == {"Nike", "Adidas", "Puma"}
        for g in groups:
            assert g.group_type == "brand"

    def test_sorts_by_delta_desc(self, batcher: SmartBatcher, sample_items: list[dict]):
        """يتحقّق أن المجموعات مرتّبة تنازلياً حسب total_price_delta."""
        groups = batcher.group_by_brand(sample_items)
        deltas = [g.total_price_delta for g in groups]
        assert deltas == sorted(deltas, reverse=True)

    def test_item_count_per_brand(self, batcher: SmartBatcher, sample_items: list[dict]):
        """يتحقّق من عدد العناصر في كل مجموعة علامة تجارية."""
        groups = batcher.group_by_brand(sample_items)
        brand_counts = {g.group_key: len(g.items) for g in groups}
        assert brand_counts["Nike"] == 2
        assert brand_counts["Adidas"] == 2
        assert brand_counts["Puma"] == 1

    def test_empty_items(self, batcher: SmartBatcher):
        """يتحقّق من التعامل مع قائمة فارغة."""
        groups = batcher.group_by_brand([])
        assert groups == []

    def test_unknown_brand(self, batcher: SmartBatcher):
        """العناصر بدون حقل brand تُجمَّع تحت 'unknown'."""
        items = [{"name": "X", "current_price": 10, "suggested_price": 15}]
        groups = batcher.group_by_brand(items)
        assert len(groups) == 1
        assert groups[0].group_key == "unknown"


# ──────────────────────────────────────────────────────────────
# اختبارات التجميع حسب الأولوية
# ──────────────────────────────────────────────────────────────

class TestGroupByPriority:
    """اختبارات group_by_priority."""

    def test_creates_three_levels(self, batcher: SmartBatcher, sample_items: list[dict]):
        """يتحقّق من إنشاء 3 مستويات أولوية."""
        groups = batcher.group_by_priority(sample_items)
        levels = [g.group_key for g in groups]
        assert len(groups) == 3
        assert "urgent" in levels
        assert "normal" in levels
        assert "low" in levels

    def test_urgent_first(self, batcher: SmartBatcher, sample_items: list[dict]):
        """يتحقّق أن المجموعة العاجلة أولاً."""
        groups = batcher.group_by_priority(sample_items)
        assert groups[0].group_key == "urgent"

    def test_priority_order(self, batcher: SmartBatcher, sample_items: list[dict]):
        """يتحقّق من ترتيب الأولويات: عاجل ← عادي ← منخفض."""
        groups = batcher.group_by_priority(sample_items)
        keys = [g.group_key for g in groups]
        assert keys == ["urgent", "normal", "low"]

    def test_confidence_boundaries(self, batcher: SmartBatcher):
        """يختبر الحدود الدقيقة: 0.9 = عاجل، 0.7 = عادي، <0.7 = منخفض."""
        items = [
            {"name": "P1", "confidence": 0.9},
            {"name": "P2", "confidence": 0.7},
            {"name": "P3", "confidence": 0.69},
        ]
        groups = batcher.group_by_priority(items)
        group_map = {g.group_key: g for g in groups}
        assert len(group_map["urgent"].items) == 1
        assert group_map["urgent"].items[0]["name"] == "P1"
        assert len(group_map["normal"].items) == 1
        assert group_map["normal"].items[0]["name"] == "P2"
        assert len(group_map["low"].items) == 1
        assert group_map["low"].items[0]["name"] == "P3"

    def test_empty_priority_levels_excluded(self, batcher: SmartBatcher):
        """المستويات الفارغة لا تُضاف."""
        items = [{"name": "U", "confidence": 0.95}]
        groups = batcher.group_by_priority(items)
        assert len(groups) == 1
        assert groups[0].group_key == "urgent"


# ──────────────────────────────────────────────────────────────
# اختبارات التجميع حسب القسم
# ──────────────────────────────────────────────────────────────

class TestGroupBySection:
    """اختبارات group_by_section."""

    def test_groups_by_section(self, batcher: SmartBatcher, sample_items: list[dict]):
        """يتحقّق من التجميع حسب القسم."""
        groups = batcher.group_by_section(sample_items)
        sections = {g.group_key for g in groups}
        assert "price_raise" in sections
        assert "price_lower" in sections
        for g in groups:
            assert g.group_type == "section"

    def test_section_item_counts(self, batcher: SmartBatcher, sample_items: list[dict]):
        """يتحقّق من عدد العناصر في كل قسم."""
        groups = batcher.group_by_section(sample_items)
        section_map = {g.group_key: len(g.items) for g in groups}
        assert section_map["price_raise"] == 3
        assert section_map["price_lower"] == 2


# ──────────────────────────────────────────────────────────────
# اختبارات ترتيب الدُّفعات
# ──────────────────────────────────────────────────────────────

class TestOptimizeBatchOrder:
    """اختبارات optimize_batch_order."""

    def test_respects_max_per_batch(self, batcher: SmartBatcher, sample_items: list[dict]):
        """يتحقّق من احترام الحدّ الأقصى لكل دُفعة."""
        groups = batcher.group_by_brand(sample_items)
        batches = batcher.optimize_batch_order(groups, max_per_batch=2)
        for batch in batches:
            assert len(batch) <= 2

    def test_total_items_preserved(self, batcher: SmartBatcher, sample_items: list[dict]):
        """يتحقّق أن إجمالي العناصر لا يتغيّر بعد التقسيم."""
        groups = batcher.group_by_brand(sample_items)
        batches = batcher.optimize_batch_order(groups, max_per_batch=3)
        total = sum(len(b) for b in batches)
        assert total == len(sample_items)

    def test_handles_empty(self, batcher: SmartBatcher):
        """يتحقّق من التعامل مع قائمة مجموعات فارغة."""
        batches = batcher.optimize_batch_order([])
        assert batches == []

    def test_single_batch(self, batcher: SmartBatcher, sample_items: list[dict]):
        """عند max_per_batch كبير يكفي لكل العناصر = دُفعة واحدة."""
        groups = batcher.group_by_brand(sample_items)
        batches = batcher.optimize_batch_order(groups, max_per_batch=100)
        assert len(batches) == 1
        assert len(batches[0]) == len(sample_items)

    def test_batch_order_follows_groups(self, batcher: SmartBatcher):
        """يتحقّق أن ترتيب العناصر في الدُّفعات يتبع ترتيب المجموعات."""
        items = [
            {"name": "Z", "brand": "Big", "current_price": 10, "suggested_price": 100},
            {"name": "A", "brand": "Small", "current_price": 10, "suggested_price": 11},
        ]
        groups = batcher.group_by_brand(items)
        # Big delta should be first
        batches = batcher.optimize_batch_order(groups, max_per_batch=50)
        assert batches[0][0]["name"] == "Z"

    def test_price_delta_field_used(self, batcher: SmartBatcher):
        """يستخدم حقل price_delta مباشرة إن وُجد."""
        items = [
            {"name": "X", "brand": "A", "price_delta": 100.0},
            {"name": "Y", "brand": "B", "price_delta": 5.0},
        ]
        groups = batcher.group_by_brand(items)
        assert groups[0].group_key == "A"
        assert groups[0].total_price_delta == 100.0
