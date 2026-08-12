"""tests/test_ai_decisions_repo.py — اختبارات وحدة لمستودع قرارات AI.

يغطي:
- إنشاء الجدول + إدراج + قراءة
- log_batch مع عدة عناصر
- resolve تحديث حالة المستخدم
- القاعدة 1: كشف الشذوذ السعري (Outlier Detection)
- القاعدة 2: حاجز الهبوط الأقصى (Max Drop)
- القاعدة 3: إجماع السوق (Market Consensus)
- compute_market_context
- get_similar_decisions + get_learning_examples (RAG)
"""
from __future__ import annotations

import os
import tempfile

import pytest

from infrastructure.db_manager import DatabaseManager
from infrastructure.ai_decisions_repo import (
    AIDecision,
    AIDecisionRepository,
    MarketContext,
    SafetyResult,
    compute_market_context,
    detect_outlier,
    validate_price_safety,
)


@pytest.fixture
def repo(tmp_path):
    """مستودع جاهز مع جدول مُنشأ في قاعدة مؤقتة."""
    db_path = str(tmp_path / "test_decisions.db")
    db = DatabaseManager(db_path)
    repo = AIDecisionRepository(db)
    repo.ensure_table()
    return repo


@pytest.fixture
def sample_decision() -> AIDecision:
    """قرار نموذجي للاختبارات."""
    return AIDecision(
        product_name="عطر ديور سوفاج 100مل",
        product_id="SKU-001",
        competitor_name="عطر سوفاج ديور 100مل",
        competitor_price=280.0,
        our_price=320.0,
        market_average=300.0,
        proposed_price=285.0,
        ai_reason="المنافس يبيع بـ280 ومتوسط السوق 300 → نقترح 285",
        ai_confidence=0.88,
        ai_decision="lower",
        ai_model="gemini-2.5-flash",
        section="price_lower",
        batch_id="batch_test_001",
    )


# ════════════════════════════════════════════════════════════════════
#  اختبارات إنشاء الجدول والإدراج
# ════════════════════════════════════════════════════════════════════

class TestTableCreation:
    def test_ensure_table_creates_table(self, repo):
        assert repo._db.table_exists("ai_decisions_log")

    def test_ensure_table_idempotent(self, repo):
        """استدعاء مكرر لا يكسر شيئاً."""
        repo.ensure_table()
        repo.ensure_table()
        assert repo._db.table_exists("ai_decisions_log")


class TestLogDecision:
    def test_log_single_decision(self, repo, sample_decision):
        row_id = repo.log_decision(sample_decision)
        assert row_id > 0

    def test_log_decision_returns_correct_data(self, repo, sample_decision):
        row_id = repo.log_decision(sample_decision)
        rows = repo.get_pending()
        assert len(rows) == 1
        assert rows[0]["product_name"] == "عطر ديور سوفاج 100مل"
        assert rows[0]["competitor_price"] == 280.0
        assert rows[0]["proposed_price"] == 285.0
        assert rows[0]["user_action"] == "pending"

    def test_log_batch(self, repo):
        decisions = [
            AIDecision(product_name=f"منتج {i}", our_price=100 + i * 10)
            for i in range(5)
        ]
        ids = repo.log_batch(decisions)
        assert len(ids) == 5
        assert all(i > 0 for i in ids)
        pending = repo.get_pending()
        assert len(pending) == 5


# ════════════════════════════════════════════════════════════════════
#  اختبارات قرار المستخدم (Resolve)
# ════════════════════════════════════════════════════════════════════

class TestResolve:
    def test_approve_decision(self, repo, sample_decision):
        row_id = repo.log_decision(sample_decision)
        repo.resolve(row_id, "approved", user_price=285.0)
        pending = repo.get_pending()
        assert len(pending) == 0

    def test_reject_decision(self, repo, sample_decision):
        row_id = repo.log_decision(sample_decision)
        repo.resolve(row_id, "rejected", note="السعر مرتفع جداً")
        # تحقق أنه لم يعد في pending
        pending = repo.get_pending()
        assert len(pending) == 0

    def test_modify_decision(self, repo, sample_decision):
        row_id = repo.log_decision(sample_decision)
        repo.resolve(row_id, "modified", user_price=290.0, note="عدلت السعر")
        recent = repo.get_recent(limit=1)
        assert recent[0]["user_action"] == "modified"
        assert recent[0]["user_price"] == 290.0
        assert recent[0]["resolved_at"] != ""

    def test_invalid_action_raises(self, repo, sample_decision):
        row_id = repo.log_decision(sample_decision)
        with pytest.raises(ValueError, match="غير صالح"):
            repo.resolve(row_id, "invalid_action")


# ════════════════════════════════════════════════════════════════════
#  اختبارات القاعدة 1: كشف الشذوذ السعري (Outlier Detection)
# ════════════════════════════════════════════════════════════════════

class TestOutlierDetection:
    def test_normal_price_not_outlier(self):
        """سعر 250 مع متوسط 300 (هبوط 17%) → ليس شاذاً."""
        assert detect_outlier(250, 300, threshold_pct=0.30) is False

    def test_very_low_price_is_outlier(self):
        """سعر 150 مع متوسط 300 (هبوط 50%) → شاذ."""
        assert detect_outlier(150, 300, threshold_pct=0.30) is True

    def test_exactly_at_threshold(self):
        """سعر 210 مع متوسط 300 (هبوط 30% بالضبط) → شاذ."""
        assert detect_outlier(210, 300, threshold_pct=0.30) is True

    def test_just_below_threshold(self):
        """سعر 211 مع متوسط 300 (هبوط ~29.7%) → ليس شاذاً."""
        assert detect_outlier(211, 300, threshold_pct=0.30) is False

    def test_zero_average_safe(self):
        """متوسط صفر → لا شذوذ."""
        assert detect_outlier(100, 0) is False

    def test_zero_price_safe(self):
        """سعر صفر → لا شذوذ."""
        assert detect_outlier(0, 300) is False


# ════════════════════════════════════════════════════════════════════
#  اختبارات القاعدة 2: حاجز الهبوط الأقصى (Max Drop)
# ════════════════════════════════════════════════════════════════════

class TestMaxDrop:
    def test_small_drop_is_safe(self):
        """هبوط 10% → آمن (الحد 15%)."""
        result = validate_price_safety(300, 270, max_drop_pct=0.15)
        assert result.is_safe is True
        assert result.flag == ""
        assert result.safe_price == 270

    def test_large_drop_triggers_flag(self):
        """هبوط 25% → غير آمن → يُوقف عند 85% من سعرنا."""
        result = validate_price_safety(300, 225, max_drop_pct=0.15)
        assert result.is_safe is False
        assert result.flag == "max_drop"
        assert result.safe_price == 255.0  # 300 * 0.85
        assert "المراجعة اليدوية" in result.warning

    def test_exactly_at_limit(self):
        """هبوط 15% بالضبط → آمن (الحد شامل)."""
        result = validate_price_safety(300, 255, max_drop_pct=0.15)
        assert result.is_safe is True

    def test_zero_our_price_skips_check(self):
        """سعرنا 0 → لا فحص هبوط."""
        result = validate_price_safety(0, 100, max_drop_pct=0.15)
        assert result.is_safe is True


# ════════════════════════════════════════════════════════════════════
#  اختبارات القاعدة 3: إجماع السوق (Market Consensus)
# ════════════════════════════════════════════════════════════════════

class TestMarketConsensus:
    def test_three_competitors_same_price(self):
        """3 منافسين بسعر 250 → إجماع."""
        ctx = compute_market_context([250, 250, 250, 350])
        assert ctx.has_consensus is True
        assert ctx.consensus_count >= 3
        assert ctx.consensus_price == 250

    def test_no_consensus_different_prices(self):
        """كل منافس بسعر مختلف → لا إجماع."""
        ctx = compute_market_context([200, 250, 300, 350])
        assert ctx.has_consensus is False

    def test_consensus_overrides_drop_when_safe(self):
        """إجماع سوقي (3 بسعر 270) + هبوط 10% → يثق بالإجماع."""
        ctx = compute_market_context([270, 270, 270, 350])
        result = validate_price_safety(
            300, 270, market_ctx=ctx, max_drop_pct=0.15,
        )
        assert result.is_safe is True
        assert result.safe_price == 270

    def test_consensus_blocked_by_max_drop(self):
        """إجماع عند 200 لكن الهبوط 33% → يُوقف رغم الإجماع."""
        ctx = compute_market_context([200, 200, 200, 400])
        result = validate_price_safety(
            300, 200, market_ctx=ctx, max_drop_pct=0.15,
        )
        assert result.is_safe is False
        assert result.flag == "max_drop"
        assert result.safe_price == 255.0  # 300 * 0.85

    def test_single_low_competitor_ignored(self):
        """منافس وحيد بـ150 والباقي بـ300 → شذوذ لا إجماع."""
        ctx = compute_market_context([150, 300, 300, 310])
        # لا إجماع (150 وحيد، 300 منافسان فقط)
        assert ctx.has_consensus is False
        # نفحص أن السعر 150 يُعتبر شاذاً
        assert detect_outlier(150, ctx.average) is True


# ════════════════════════════════════════════════════════════════════
#  اختبارات compute_market_context
# ════════════════════════════════════════════════════════════════════

class TestMarketContext:
    def test_empty_prices(self):
        ctx = compute_market_context([])
        assert ctx.competitor_count == 0
        assert ctx.average == 0

    def test_single_price(self):
        ctx = compute_market_context([250])
        assert ctx.average == 250
        assert ctx.min_price == 250
        assert ctx.competitor_count == 1

    def test_average_calculation(self):
        ctx = compute_market_context([200, 300, 400])
        assert ctx.average == 300.0

    def test_median_odd(self):
        ctx = compute_market_context([100, 200, 300])
        assert ctx.median == 200.0

    def test_median_even(self):
        ctx = compute_market_context([100, 200, 300, 400])
        assert ctx.median == 250.0

    def test_ignores_zero_prices(self):
        ctx = compute_market_context([0, 0, 250, 300])
        assert ctx.competitor_count == 2
        assert ctx.average == 275.0

    def test_consensus_with_tolerance(self):
        """أسعار قريبة (±3%) تُعتبر نفس السعر."""
        # 250 و 255 → فرق 2% → نفس المجموعة
        ctx = compute_market_context([250, 252, 254, 400])
        assert ctx.consensus_count >= 3


# ════════════════════════════════════════════════════════════════════
#  اختبارات RAG (بحث مشابه + أمثلة تعلّم)
# ════════════════════════════════════════════════════════════════════

class TestRAGQueries:
    def test_get_similar_decisions(self, repo):
        """يجلب قرارات مشابهة بعد التصويت."""
        d1 = AIDecision(product_name="عطر ديور سوفاج 100مل", our_price=300)
        d2 = AIDecision(product_name="عطر ديور سوفاج 50مل", our_price=200)
        d3 = AIDecision(product_name="عطر شانيل بلو 100مل", our_price=350)
        ids = repo.log_batch([d1, d2, d3])

        # حل القرارين الأولين
        repo.resolve(ids[0], "approved", user_price=280)
        repo.resolve(ids[1], "modified", user_price=190)

        similar = repo.get_similar_decisions("عطر ديور سوفاج", limit=5)
        assert len(similar) == 2  # d1 و d2 فقط (مطابقة + محلولة)
        # d3 (شانيل) لا يظهر

    def test_get_learning_examples_priority(self, repo):
        """modified أولاً ثم rejected ثم approved."""
        d1 = AIDecision(product_name="منتج أ", our_price=100)
        d2 = AIDecision(product_name="منتج ب", our_price=200)
        d3 = AIDecision(product_name="منتج ج", our_price=300)
        ids = repo.log_batch([d1, d2, d3])

        repo.resolve(ids[0], "approved", user_price=100)
        repo.resolve(ids[1], "modified", user_price=180)
        repo.resolve(ids[2], "rejected", note="غالي")

        examples = repo.get_learning_examples(limit=10)
        assert len(examples) == 3
        # modified يأتي أولاً
        assert examples[0]["user_action"] == "modified"
        assert examples[1]["user_action"] == "rejected"
        assert examples[2]["user_action"] == "approved"

    def test_pending_not_in_learning(self, repo):
        """القرارات المعلّقة لا تظهر في أمثلة التعلّم."""
        d = AIDecision(product_name="منتج معلّق", our_price=100)
        repo.log_decision(d)
        examples = repo.get_learning_examples()
        assert len(examples) == 0


# ════════════════════════════════════════════════════════════════════
#  اختبارات الإحصاءات
# ════════════════════════════════════════════════════════════════════

class TestStats:
    def test_stats_empty(self, repo):
        stats = repo.get_stats()
        assert stats["total"] == 0

    def test_stats_after_decisions(self, repo):
        decisions = [
            AIDecision(product_name=f"م {i}", our_price=100)
            for i in range(4)
        ]
        ids = repo.log_batch(decisions)
        repo.resolve(ids[0], "approved")
        repo.resolve(ids[1], "rejected")
        repo.resolve(ids[2], "modified", user_price=90)
        # ids[3] يبقى pending

        stats = repo.get_stats()
        assert stats["approved"] == 1
        assert stats["rejected"] == 1
        assert stats["modified"] == 1
        assert stats["pending"] == 1
        assert stats["total"] == 4


# ════════════════════════════════════════════════════════════════════
#  اختبار batch_id
# ════════════════════════════════════════════════════════════════════

class TestBatch:
    def test_batch_id_unique(self, repo):
        id1 = AIDecisionRepository.new_batch_id()
        id2 = AIDecisionRepository.new_batch_id()
        assert id1 != id2
        assert id1.startswith("batch_")

    def test_get_by_batch(self, repo):
        batch_id = "test_batch_123"
        d1 = AIDecision(product_name="م1", batch_id=batch_id)
        d2 = AIDecision(product_name="م2", batch_id=batch_id)
        d3 = AIDecision(product_name="م3", batch_id="other_batch")
        repo.log_batch([d1, d2, d3])

        batch_rows = repo.get_by_batch(batch_id)
        assert len(batch_rows) == 2


# ════════════════════════════════════════════════════════════════════
#  اختبار شامل: السيناريو الكامل
# ════════════════════════════════════════════════════════════════════

class TestFullScenario:
    def test_end_to_end_pricing_flow(self, repo):
        """سيناريو كامل: حساب سياق → فحص أمان → تسجيل → قرار مستخدم."""
        # 1. بيانات السوق: 4 منافسين
        prices = [250, 260, 255, 400]
        ctx = compute_market_context(prices)
        assert ctx.average > 0

        # 2. فحص الأمان لسعر مقترح 240 (هبوط 20% من 300)
        result = validate_price_safety(
            our_current_price=300,
            proposed_price=240,
            market_ctx=ctx,
            max_drop_pct=0.15,
        )
        assert result.is_safe is False  # هبوط > 15%

        # 3. تسجيل القرار مع علم الأمان
        decision = AIDecision(
            product_name="عطر تست",
            our_price=300,
            proposed_price=result.safe_price,
            market_average=ctx.average,
            safety_flag=result.flag,
            ai_reason=result.warning,
            batch_id="test_e2e",
        )
        row_id = repo.log_decision(decision)
        assert row_id > 0

        # 4. المستخدم يراجع ويعدّل السعر
        repo.resolve(row_id, "modified", user_price=260, note="عدلت يدوياً")

        # 5. التعلّم: القرار يظهر في الأمثلة
        examples = repo.get_learning_examples()
        assert len(examples) == 1
        assert examples[0]["user_action"] == "modified"
        assert examples[0]["user_price"] == 260
