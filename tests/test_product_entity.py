"""tests/test_product_entity.py — اختبارات وحدة لكيان المنتج وآلة الحالات.

≥ 15 اختبار يغطي:
- إنشاء الكيان من مصادر مختلفة
- آلة الحالات (تحولات صحيحة + مرفوضة)
- تسجيل الأحداث (سعر، منافس، قرار مستخدم، تحليل AI)
- التسلسل (to_dict / from_dict)
- الخصائص المشتقة
- ثبات entity_id عبر التحليلات
"""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

from core.product_entity import (
    EventType,
    InvalidTransitionError,
    ProductEntity,
    ProductEvent,
    ProductState,
    VALID_TRANSITIONS,
    make_entity_id,
)


# ════════════════════════════════════════════════════════════════════
#  إنشاء الكيان
# ════════════════════════════════════════════════════════════════════

class TestProductEntityCreation:
    """اختبارات إنشاء الكيان."""

    def test_create_from_product(self):
        """إنشاء كيان من بيانات منتج أساسية."""
        e = ProductEntity.from_product("SKU-001", "Dior Sauvage 100ml", 450.0, brand="Dior")
        assert e.product_id == "SKU-001"
        assert e.name == "Dior Sauvage 100ml"
        assert e.current_price == 450.0
        assert e.brand == "Dior"
        assert e.state == ProductState.DISCOVERED
        assert len(e.events) == 0
        assert len(e.entity_id) == 16

    def test_create_from_row_arabic_columns(self):
        """إنشاء كيان من صف DataFrame بأعمدة عربية."""
        row = {
            "معرف_المنتج": "P-200",
            "المنتج": "شانيل بلو 50مل",
            "السعر": 380.0,
            "الماركة": "شانيل",
        }
        e = ProductEntity.from_row(row)
        assert e.product_id == "P-200"
        assert e.name == "شانيل بلو 50مل"
        assert e.current_price == 380.0
        assert e.brand == "شانيل"

    def test_create_from_row_english_columns(self):
        """إنشاء كيان من صف بأعمدة إنجليزية (fallback)."""
        row = {"product_id": "E-100", "name": "Tom Ford Oud", "price": 900}
        e = ProductEntity.from_row(row)
        assert e.product_id == "E-100"
        assert e.name == "Tom Ford Oud"
        assert e.current_price == 900.0

    def test_create_with_invalid_price(self):
        """سعر غير رقمي يُحوَّل لـ 0."""
        row = {"معرف_المنتج": "X", "المنتج": "Test", "السعر": "invalid"}
        e = ProductEntity.from_row(row)
        assert e.current_price == 0.0

    def test_default_state_is_discovered(self):
        """الحالة الافتراضية discovered."""
        e = ProductEntity()
        assert e.state == ProductState.DISCOVERED


# ════════════════════════════════════════════════════════════════════
#  entity_id الثابت
# ════════════════════════════════════════════════════════════════════

class TestEntityIdStability:
    """اختبارات ثبات entity_id عبر التحليلات."""

    def test_same_input_same_id(self):
        """نفس (product_id, source) → نفس entity_id دائماً."""
        id1 = make_entity_id("SKU-001", "analysis_v1")
        id2 = make_entity_id("SKU-001", "analysis_v1")
        assert id1 == id2

    def test_different_source_different_id(self):
        """مصادر مختلفة → entity_id مختلف."""
        id1 = make_entity_id("SKU-001", "source_a")
        id2 = make_entity_id("SKU-001", "source_b")
        assert id1 != id2

    def test_empty_product_id_gives_random(self):
        """product_id فارغ → UUID عشوائي (مختلف كل مرة)."""
        id1 = make_entity_id("", "")
        id2 = make_entity_id("", "")
        assert id1 != id2

    def test_from_product_stable_id(self):
        """from_product ينتج entity_id ثابت."""
        e1 = ProductEntity.from_product("SKU-100", "Test Product", 100.0)
        e2 = ProductEntity.from_product("SKU-100", "Test Product", 200.0)
        assert e1.entity_id == e2.entity_id  # نفس المعرف حتى مع سعر مختلف


# ════════════════════════════════════════════════════════════════════
#  آلة الحالات
# ════════════════════════════════════════════════════════════════════

class TestStateMachine:
    """اختبارات آلة الحالات."""

    def test_valid_transition_discovered_to_matched(self):
        """تحوّل صحيح: discovered → matched."""
        e = ProductEntity(state=ProductState.DISCOVERED)
        event = e.transition(ProductState.MATCHED, actor="engine")
        assert e.state == ProductState.MATCHED
        assert event.event_type == EventType.STATE_CHANGE
        assert event.old_state == "discovered"
        assert event.new_state == "matched"
        assert event.actor == "engine"
        assert len(e.events) == 1

    def test_valid_full_lifecycle(self):
        """دورة حياة كاملة: discovered → ... → sent."""
        e = ProductEntity(state=ProductState.DISCOVERED)
        e.transition(ProductState.MATCHED)
        e.transition(ProductState.CLASSIFIED)
        e.transition(ProductState.PRICED)
        e.transition(ProductState.AI_REVIEWED)
        e.transition(ProductState.PENDING_APPROVAL)
        e.transition(ProductState.APPROVED, actor="user")
        e.transition(ProductState.SENT)
        assert e.state == ProductState.SENT
        assert len(e.events) == 7

    def test_invalid_transition_raises(self):
        """تحوّل غير مسموح يرفع InvalidTransitionError."""
        e = ProductEntity(state=ProductState.DISCOVERED)
        with pytest.raises(InvalidTransitionError) as exc_info:
            e.transition(ProductState.SENT)
        assert "discovered" in str(exc_info.value)
        assert "sent" in str(exc_info.value)
        assert e.state == ProductState.DISCOVERED  # الحالة لم تتغير

    def test_rejected_can_re_enter_classified(self):
        """المرفوض يمكنه الدخول لإعادة التصنيف."""
        e = ProductEntity(state=ProductState.REJECTED)
        e.transition(ProductState.CLASSIFIED)
        assert e.state == ProductState.CLASSIFIED

    def test_auto_approved_goes_to_sent(self):
        """الموافقة التلقائية تؤدي مباشرة للإرسال."""
        e = ProductEntity(state=ProductState.AUTO_APPROVED)
        e.transition(ProductState.SENT)
        assert e.state == ProductState.SENT

    def test_re_evaluation_cycle(self):
        """دورة إعادة التقييم: tracked → re_evaluated → classified."""
        e = ProductEntity(state=ProductState.TRACKED)
        e.transition(ProductState.RE_EVALUATED)
        e.transition(ProductState.CLASSIFIED)
        assert e.state == ProductState.CLASSIFIED

    def test_missing_path(self):
        """المسار المفقود: discovered → unmatched → missing → enriched → sent."""
        e = ProductEntity(state=ProductState.DISCOVERED)
        e.transition(ProductState.UNMATCHED)
        e.transition(ProductState.MISSING_CANDIDATE)
        e.transition(ProductState.ENRICHED)
        e.transition(ProductState.SENT)
        assert e.state == ProductState.SENT

    def test_all_states_have_transitions(self):
        """كل حالة لها تحولات مُعرَّفة."""
        for state in ProductState:
            assert state in VALID_TRANSITIONS, f"{state.value} ليس في خريطة التحولات"

    def test_transition_data_preserved(self):
        """البيانات المرفقة بالتحوّل تُحفظ في الحدث."""
        e = ProductEntity(state=ProductState.DISCOVERED)
        event = e.transition(
            ProductState.MATCHED,
            data={"match_score": 92.5, "competitor": "عطر كوم"},
        )
        assert event.data["match_score"] == 92.5
        assert event.data["competitor"] == "عطر كوم"


# ════════════════════════════════════════════════════════════════════
#  تسجيل الأحداث
# ════════════════════════════════════════════════════════════════════

class TestEventRecording:
    """اختبارات تسجيل الأحداث المختلفة."""

    def test_record_price_change(self):
        """تسجيل تغيير سعري."""
        e = ProductEntity(current_price=400.0)
        event = e.record_price_change(350.0, "competitor_match")
        assert e.current_price == 350.0
        assert event.event_type == EventType.PRICE_UPDATE
        assert event.data["old_price"] == 400.0
        assert event.data["new_price"] == 350.0
        assert event.data["delta_pct"] == -12.5
        assert len(e.price_history) == 1
        assert e.price_history[0]["price"] == 350.0

    def test_record_price_change_from_zero(self):
        """تسجيل تغيير سعري من سعر صفر (لا division by zero)."""
        e = ProductEntity(current_price=0.0)
        event = e.record_price_change(100.0, "initial")
        assert event.data["delta_pct"] == 0.0
        assert e.current_price == 100.0

    def test_record_competitor_price(self):
        """تسجيل سعر منافس."""
        e = ProductEntity()
        event = e.record_competitor_price("عطر كوم", 380.0)
        assert e.competitor_prices["عطر كوم"] == 380.0
        assert event.event_type == EventType.COMPETITOR_CHANGE
        assert event.data["store"] == "عطر كوم"

    def test_record_competitor_price_update(self):
        """تحديث سعر منافس موجود يحفظ السعر القديم."""
        e = ProductEntity(competitor_prices={"عطر كوم": 380.0})
        event = e.record_competitor_price("عطر كوم", 350.0)
        assert event.data["old_price"] == 380.0
        assert event.data["new_price"] == 350.0
        assert e.competitor_prices["عطر كوم"] == 350.0

    def test_record_user_decision(self):
        """تسجيل قرار مستخدم."""
        e = ProductEntity(suggested_price=350.0, ai_confidence=88.0)
        event = e.record_user_decision("modified", user_price=360.0, note="أرفع قليلاً")
        assert event.event_type == EventType.USER_DECISION
        assert event.actor == "user"
        assert event.data["action"] == "modified"
        assert event.data["user_price"] == 360.0
        assert event.data["ai_suggested"] == 350.0

    def test_record_ai_analysis(self):
        """تسجيل تحليل AI."""
        e = ProductEntity(current_price=400.0)
        event = e.record_ai_analysis(
            suggested_price=370.0,
            confidence=85.5,
            reasoning="السعر أعلى من المتوسط",
            model="gemini-2.5-flash",
        )
        assert e.suggested_price == 370.0
        assert e.ai_confidence == 85.5
        assert event.event_type == EventType.AI_ANALYSIS
        assert event.data["model"] == "gemini-2.5-flash"


# ════════════════════════════════════════════════════════════════════
#  التسلسل
# ════════════════════════════════════════════════════════════════════

class TestSerialization:
    """اختبارات التحويل من/إلى قاموس."""

    def test_entity_to_dict_roundtrip(self):
        """to_dict → from_dict يحافظ على كل البيانات."""
        original = ProductEntity.from_product(
            "SKU-999", "Versace Eros 200ml", 600.0, brand="Versace",
        )
        original.section = "price_raise"
        original.ai_confidence = 92.0
        original.competitor_prices = {"store_a": 550.0, "store_b": 580.0}

        d = original.to_dict()
        restored = ProductEntity.from_dict(d)

        assert restored.entity_id == original.entity_id
        assert restored.product_id == "SKU-999"
        assert restored.name == "Versace Eros 200ml"
        assert restored.brand == "Versace"
        assert restored.state == ProductState.DISCOVERED
        assert restored.current_price == 600.0
        assert restored.section == "price_raise"
        assert restored.ai_confidence == 92.0
        assert restored.competitor_prices == {"store_a": 550.0, "store_b": 580.0}

    def test_from_dict_invalid_state_defaults(self):
        """حالة غير معروفة تُعاد لـ discovered."""
        d = {"state": "nonexistent_state", "name": "Test"}
        e = ProductEntity.from_dict(d)
        assert e.state == ProductState.DISCOVERED

    def test_event_to_dict_roundtrip(self):
        """ProductEvent to_dict → from_dict roundtrip."""
        event = ProductEvent(
            event_type=EventType.PRICE_UPDATE,
            actor="user",
            data={"old_price": 100, "new_price": 90},
        )
        d = event.to_dict()
        restored = ProductEvent.from_dict(d)
        assert restored.event_id == event.event_id
        assert restored.event_type == EventType.PRICE_UPDATE
        assert restored.actor == "user"
        assert restored.data["new_price"] == 90


# ════════════════════════════════════════════════════════════════════
#  الخصائص المشتقة
# ════════════════════════════════════════════════════════════════════

class TestDerivedProperties:
    """اختبارات الخصائص المشتقة."""

    def test_timeline_sorted(self):
        """الخط الزمني مرتّب زمنياً."""
        e = ProductEntity()
        e.record_price_change(100.0, "a")
        e.record_price_change(200.0, "b")
        e.record_price_change(300.0, "c")
        timeline = e.timeline
        assert len(timeline) == 3
        # مرتب زمنياً (الأقدم أولاً)
        for i in range(len(timeline) - 1):
            assert timeline[i]["timestamp"] <= timeline[i + 1]["timestamp"]

    def test_event_count(self):
        """عدد الأحداث يُحسب صحيحاً."""
        e = ProductEntity(state=ProductState.DISCOVERED)
        assert e.event_count == 0
        e.transition(ProductState.MATCHED)
        assert e.event_count == 1
        e.record_price_change(100.0, "test")
        assert e.event_count == 2

    def test_is_actionable(self):
        """المنتجات في حالة تتطلب إجراءً."""
        assert ProductEntity(state=ProductState.PENDING_APPROVAL).is_actionable
        assert ProductEntity(state=ProductState.AI_REVIEWED).is_actionable
        assert not ProductEntity(state=ProductState.SENT).is_actionable
        assert not ProductEntity(state=ProductState.DISCOVERED).is_actionable

    def test_is_terminal(self):
        """المنتجات في حالة نهائية."""
        assert ProductEntity(state=ProductState.SENT).is_terminal
        assert ProductEntity(state=ProductState.TRACKED).is_terminal
        assert not ProductEntity(state=ProductState.APPROVED).is_terminal

    def test_min_competitor_price(self):
        """أقل سعر منافس."""
        e = ProductEntity(competitor_prices={"a": 100, "b": 80, "c": 120})
        assert e.min_competitor_price == 80

    def test_min_competitor_price_empty(self):
        """لا منافسين → None."""
        e = ProductEntity()
        assert e.min_competitor_price is None

    def test_days_since_last_update(self):
        """حساب الأيام منذ آخر تحديث."""
        e = ProductEntity(updated_at=datetime.now(timezone.utc) - timedelta(days=3))
        assert 2.9 < e.days_since_last_update < 3.1

