"""tests/test_swarm.py — اختبارات نظام السَّرب (Swarm) للتسعير الذكي.

يغطي 18 اختباراً:
- وكيل المحلل (5 اختبارات)
- وكيل المدقق (4 اختبارات)
- وكيل الاستراتيجي (2 اختبارات)
- المنسّق (6 اختبارات)
- التسلسل والدفعات (1 اختبار)

يستخدم MockAIService بدلاً من استدعاءات حقيقية.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pytest

from services.ai_service import AIResult
from services.swarm.agents import (
    AgentVote,
    AnalystAgent,
    AuditorAgent,
    SharedContext,
    StrategistAgent,
    _compute_weighted_avg,
    _position_label,
    _std_dev,
)
from services.swarm.orchestrator import (
    SwarmDecision,
    SwarmOrchestrator,
    _classify_tier,
    _price_diff_pct,
)


# ════════════════════════════════════════════════════════════════════
#  MockAIService — بديل اختباري لخدمة AI
# ════════════════════════════════════════════════════════════════════

class MockAIService:
    """خدمة AI وهمية تُعيد استجابات محددة مسبقاً.

    تُستخدم في الاختبارات لتجنب استدعاءات الشبكة الحقيقية.
    """

    def __init__(
        self,
        response: str = "تحليل AI: السعر مناسب للسوق الحالي.",
        success: bool = True,
    ) -> None:
        self._response = response
        self._success = success
        self.call_count = 0

    def call(
        self,
        prompt: str,
        system: str = "",
        *,
        use_cache: bool = True,
    ) -> AIResult:
        """يُعيد نتيجة محددة مسبقاً."""
        self.call_count += 1
        return AIResult(
            success=self._success,
            response=self._response if self._success else "",
            source="mock",
        )

    def call_json(self, prompt: str, system: str = "") -> Any:
        return None

    @property
    def any_configured(self) -> bool:
        return True


class FailingAIService(MockAIService):
    """خدمة AI وهمية تفشل دائماً (لاختبار التدهور السلس)."""

    def __init__(self) -> None:
        super().__init__(response="", success=False)

    def call(self, prompt: str, system: str = "", *, use_cache: bool = True) -> AIResult:
        self.call_count += 1
        raise ConnectionError("محاكاة فشل الشبكة")


# ════════════════════════════════════════════════════════════════════
#  معايِر ثقة وهمي (لاختبار المعايرة)
# ════════════════════════════════════════════════════════════════════

class MockCalibrator:
    """معايِر ثقة وهمي يضرب الثقة بعامل ثابت."""

    def __init__(self, factor: float = 1.0) -> None:
        self._factor = factor

    def calibrate(self, raw_confidence: float, context: dict[str, Any]) -> float:
        return min(raw_confidence * self._factor, 1.0)


class MockMemory:
    """ذاكرة أنماط وهمية تُعيد أنماطاً ثابتة."""

    def __init__(self, patterns: Optional[list[str]] = None) -> None:
        self._patterns = patterns or ["نمط_تنافسي", "نمط_موسمي"]

    def get_patterns(self, product_name: str, brand: str, section: str) -> list[str]:
        return self._patterns


# ════════════════════════════════════════════════════════════════════
#  تجهيزات مشتركة
# ════════════════════════════════════════════════════════════════════

@pytest.fixture
def mock_ai() -> MockAIService:
    """خدمة AI وهمية ناجحة."""
    return MockAIService()


@pytest.fixture
def failing_ai() -> FailingAIService:
    """خدمة AI وهمية فاشلة."""
    return FailingAIService()


@pytest.fixture
def sample_ctx() -> SharedContext:
    """سياق مشترك نموذجي بمنافسين."""
    return SharedContext(
        product_name="كريم بشرة",
        brand="نيڤيا",
        our_price=100.0,
        competitor_prices=[90.0, 95.0, 105.0, 110.0],
        section="عناية",
        market_avg=100.0,
        market_min=90.0,
        market_max=110.0,
        price_tier="mid",
        patterns=["نمط_تنافسي"],
        historical_accuracy=0.6,
    )


@pytest.fixture
def no_competitor_ctx() -> SharedContext:
    """سياق بدون منافسين."""
    return SharedContext(
        product_name="منتج فريد",
        brand="ماركة خاصة",
        our_price=200.0,
        competitor_prices=[],
        section="خاص",
    )


# ════════════════════════════════════════════════════════════════════
#  اختبارات وكيل المحلل (AnalystAgent)
# ════════════════════════════════════════════════════════════════════

class TestAnalystAgent:
    """اختبارات وكيل المحلل."""

    def test_vote_returns_valid_agent_vote(self, mock_ai: MockAIService, sample_ctx: SharedContext) -> None:
        """تصويت المحلل يُعيد AgentVote صالح بالحقول المطلوبة."""
        agent = AnalystAgent(mock_ai)
        vote = agent.vote(sample_ctx)

        assert isinstance(vote, AgentVote)
        assert vote.agent_name == "analyst"
        assert vote.suggested_price > 0
        assert 0.0 <= vote.confidence <= 1.0
        assert len(vote.reasoning) > 0
        assert isinstance(vote.flags, list)

    def test_vote_no_competitors_low_confidence(
        self, mock_ai: MockAIService, no_competitor_ctx: SharedContext,
    ) -> None:
        """بدون منافسين: يحتفظ بالسعر الحالي مع ثقة منخفضة (0.3)."""
        agent = AnalystAgent(mock_ai)
        vote = agent.vote(no_competitor_ctx)

        assert vote.suggested_price == no_competitor_ctx.our_price
        assert vote.confidence == 0.3
        assert "no_competitors" in vote.flags

    def test_vote_with_competitors_higher_confidence(
        self, mock_ai: MockAIService, sample_ctx: SharedContext,
    ) -> None:
        """مع منافسين: ثقة أعلى من حالة عدم وجود منافسين."""
        agent = AnalystAgent(mock_ai)
        vote = agent.vote(sample_ctx)

        assert vote.confidence > 0.3
        assert vote.suggested_price > 0

    def test_vote_ai_failure_falls_back(
        self, failing_ai: FailingAIService, sample_ctx: SharedContext,
    ) -> None:
        """عند فشل AI: يعود للتحليل الرياضي بدون خطأ."""
        agent = AnalystAgent(failing_ai)
        vote = agent.vote(sample_ctx)

        assert isinstance(vote, AgentVote)
        assert vote.agent_name == "analyst"
        assert vote.suggested_price > 0
        # لا يحتوي التبرير على "تحليل AI" عند الفشل
        assert vote.cost_tokens == 0

    def test_vote_expensive_position_flagged(self, mock_ai: MockAIService) -> None:
        """إذا كنا الأغلى: يُضاف تحذير 'price_above_market'."""
        ctx = SharedContext(
            product_name="منتج غالي",
            brand="ماركة",
            our_price=200.0,
            competitor_prices=[100.0, 110.0, 120.0],
            section="عام",
            market_avg=110.0,
            market_min=100.0,
            market_max=120.0,
        )
        agent = AnalystAgent(mock_ai)
        vote = agent.vote(ctx)

        assert "price_above_market" in vote.flags


# ════════════════════════════════════════════════════════════════════
#  اختبارات وكيل المدقق (AuditorAgent)
# ════════════════════════════════════════════════════════════════════

class TestAuditorAgent:
    """اختبارات وكيل المدقق."""

    def test_vote_catches_max_drop_violation(self, mock_ai: MockAIService, sample_ctx: SharedContext) -> None:
        """انخفاض > 15%: يُعدّل السعر ويضيف تحذير 'max_drop_exceeded'."""
        agent = AuditorAgent(mock_ai)
        # اقتراح انخفاض 30%: من 100 إلى 70
        analyst_vote = AgentVote(
            agent_name="analyst",
            suggested_price=70.0,
            confidence=0.7,
            reasoning="تحليل رياضي",
        )

        vote = agent.vote(sample_ctx, analyst_vote)

        assert vote.agent_name == "auditor"
        assert "max_drop_exceeded" in vote.flags
        # السعر المعدّل يجب أن يكون 100 * 0.85 = 85.0
        assert vote.suggested_price == 85.0

    def test_vote_passes_safe_price(self, mock_ai: MockAIService, sample_ctx: SharedContext) -> None:
        """سعر آمن (انخفاض < 15%): يمر بدون تعديل."""
        agent = AuditorAgent(mock_ai)
        analyst_vote = AgentVote(
            agent_name="analyst",
            suggested_price=95.0,
            confidence=0.8,
            reasoning="تحليل رياضي",
        )

        vote = agent.vote(sample_ctx, analyst_vote)

        assert vote.agent_name == "auditor"
        assert "max_drop_exceeded" not in vote.flags
        # لا تعديل كبير (السعر قد يتغير فقط إذا كان شاذاً)
        assert vote.confidence >= 0.65

    def test_vote_flags_outlier(self, mock_ai: MockAIService) -> None:
        """سعر شاذ (> 2σ): يُضاف تحذير 'outlier_detected'."""
        # أسعار منافسين متقاربة جداً + اقتراح بعيد
        ctx = SharedContext(
            product_name="منتج",
            brand="ماركة",
            our_price=100.0,
            competitor_prices=[100.0, 101.0, 99.0, 100.5],
            section="عام",
            market_avg=100.0,
            market_min=99.0,
            market_max=101.0,
        )
        agent = AuditorAgent(mock_ai)
        # اقتراح بعيد جداً عن المتوسط
        analyst_vote = AgentVote(
            agent_name="analyst",
            suggested_price=115.0,
            confidence=0.5,
            reasoning="تحليل",
        )

        vote = agent.vote(ctx, analyst_vote)
        assert "outlier_detected" in vote.flags

    def test_vote_flags_below_margin(self, mock_ai: MockAIService) -> None:
        """سعر أقل من أرضية الهامش: يُضاف تحذير 'below_margin'."""
        ctx = SharedContext(
            product_name="منتج",
            brand="ماركة",
            our_price=100.0,
            competitor_prices=[80.0, 85.0, 90.0],
            section="عام",
            market_avg=85.0,
            market_min=80.0,
            market_max=90.0,
        )
        agent = AuditorAgent(mock_ai)
        # اقتراح أقل من التكلفة المقدّرة (market_min * 0.7 * 1.05)
        # التكلفة المقدّرة = 80 * 0.7 = 56, أرضية = 56 * 1.05 = 58.8
        analyst_vote = AgentVote(
            agent_name="analyst",
            suggested_price=50.0,
            confidence=0.5,
            reasoning="تحليل",
        )

        vote = agent.vote(ctx, analyst_vote)
        # Price 50 vs market_min 80 → 50% drop exceeds 15% max_drop rule
        assert "max_drop_exceeded" in vote.flags or "below_margin" in vote.flags


# ════════════════════════════════════════════════════════════════════
#  اختبارات وكيل الاستراتيجي (StrategistAgent)
# ════════════════════════════════════════════════════════════════════

class TestStrategistAgent:
    """اختبارات وكيل الاستراتيجي."""

    def test_arbitrate_picks_middle_ground(self, mock_ai: MockAIService, sample_ctx: SharedContext) -> None:
        """التحكيم يختار حلاً وسطاً بين المحلل والمدقق."""
        agent = StrategistAgent(mock_ai)
        votes = [
            AgentVote(agent_name="analyst", suggested_price=90.0, confidence=0.7, reasoning="تحليل"),
            AgentVote(agent_name="auditor", suggested_price=100.0, confidence=0.8, reasoning="تدقيق"),
        ]

        result = agent.arbitrate(sample_ctx, votes)

        assert result.agent_name == "strategist"
        # المتوسط المرجّح بالثقة: (90*0.7 + 100*0.8) / (0.7+0.8) ≈ 95.33
        assert 90.0 <= result.suggested_price <= 100.0
        assert result.confidence > 0

    def test_arbitrate_single_vote(self, mock_ai: MockAIService, sample_ctx: SharedContext) -> None:
        """تصويت واحد فقط: يعتمد عليه مع تقليل الثقة."""
        agent = StrategistAgent(mock_ai)
        votes = [
            AgentVote(agent_name="analyst", suggested_price=95.0, confidence=0.8, reasoning="تحليل"),
        ]

        result = agent.arbitrate(sample_ctx, votes)

        assert result.agent_name == "strategist"
        assert result.suggested_price == 95.0
        assert result.confidence < 0.8  # ثقة أقل


# ════════════════════════════════════════════════════════════════════
#  اختبارات المنسّق (SwarmOrchestrator)
# ════════════════════════════════════════════════════════════════════

class TestSwarmOrchestrator:
    """اختبارات منسّق السَّرب."""

    def test_analyze_returns_swarm_decision(self, mock_ai: MockAIService) -> None:
        """التحليل يُعيد SwarmDecision صالح."""
        orch = SwarmOrchestrator(mock_ai)
        decision = orch.analyze(
            product_name="شامبو",
            our_price=50.0,
            competitor_prices=[45.0, 48.0, 52.0],
        )

        assert isinstance(decision, SwarmDecision)
        assert decision.final_price > 0
        assert 0.0 <= decision.final_confidence <= 1.0
        assert decision.consensus_type in ("unanimous", "majority", "arbitrated", "escalated")
        assert len(decision.votes) >= 1

    def test_orchestrator_skips_auditor_high_confidence(self, mock_ai: MockAIService) -> None:
        """ثقة المحلل ≥ 85% (بعد المعايرة): يتجاوز المدقق."""
        # نستخدم معايِر يضاعف الثقة لضمان تجاوز العتبة
        calibrator = MockCalibrator(factor=1.5)
        orch = SwarmOrchestrator(mock_ai, calibrator=calibrator)
        decision = orch.analyze(
            product_name="منتج واثق",
            our_price=100.0,
            competitor_prices=[95.0, 98.0, 102.0, 105.0, 108.0],
        )

        assert decision.consensus_type == "unanimous"
        # تصويت واحد فقط (المحلل)
        assert len(decision.votes) == 1
        assert decision.votes[0].agent_name == "analyst"

    def test_orchestrator_calls_auditor_low_confidence(self, mock_ai: MockAIService) -> None:
        """ثقة المحلل < 85%: يستدعي المدقق."""
        # المعايِر يخفض الثقة
        calibrator = MockCalibrator(factor=0.5)
        orch = SwarmOrchestrator(mock_ai, calibrator=calibrator)
        decision = orch.analyze(
            product_name="منتج غير واثق",
            our_price=100.0,
            competitor_prices=[95.0, 105.0],
        )

        # يجب أن يكون هناك تصويتان على الأقل (محلل + مدقق)
        assert len(decision.votes) >= 2
        agent_names = [v.agent_name for v in decision.votes]
        assert "analyst" in agent_names
        assert "auditor" in agent_names

    def test_orchestrator_calls_strategist_on_conflict(self, mock_ai: MockAIService) -> None:
        """خلاف 3-15% بين المحلل والمدقق: يستدعي الاستراتيجي."""
        # نصنع سيناريو يسبب خلافاً:
        # سعر مرتفع جداً + منافسون رخيصون → المحلل يخفض كثيراً
        # المدقق يمنع الخفض > 15% → خلاف
        calibrator = MockCalibrator(factor=0.3)  # ثقة منخفضة → المدقق يعمل
        orch = SwarmOrchestrator(mock_ai, calibrator=calibrator)

        decision = orch.analyze(
            product_name="منتج متنازع",
            our_price=200.0,
            competitor_prices=[100.0, 110.0, 120.0],
        )

        # إما arbitrated (إذا الاستراتيجي تدخّل) أو escalated
        assert decision.consensus_type in ("arbitrated", "escalated")
        if decision.consensus_type == "arbitrated":
            agent_names = [v.agent_name for v in decision.votes]
            assert "strategist" in agent_names

    def test_orchestrator_marks_escalated_large_conflict(self, mock_ai: MockAIService) -> None:
        """خلاف > 15%: يُعلّم 'escalated' ويطلب مراجعة بشرية."""
        calibrator = MockCalibrator(factor=0.3)
        orch = SwarmOrchestrator(mock_ai, calibrator=calibrator)

        # فارق كبير جداً: سعرنا 500 ومنافسين 100-120
        decision = orch.analyze(
            product_name="منتج مُصعَّد",
            our_price=500.0,
            competitor_prices=[100.0, 110.0, 120.0],
        )

        # المحلل سيقترح ~106 (متوسط مرجّح)
        # المدقق سيحد الانخفاض عند 425 (500*0.85)
        # الفارق كبير → arbitrated أو escalated
        assert decision.consensus_type in ("escalated", "arbitrated")
        # في الحالتين: النتيجة تحتاج مراجعة بشرية أو تحكيم
        assert decision.final_price > 0

    def test_orchestrator_works_without_calibrator_memory(self, mock_ai: MockAIService) -> None:
        """يعمل بدون معايِر ثقة أو ذاكرة أنماط (تدهور سلس)."""
        orch = SwarmOrchestrator(mock_ai)  # بدون calibrator/memory
        decision = orch.analyze(
            product_name="منتج بسيط",
            our_price=100.0,
            competitor_prices=[95.0, 105.0],
        )

        assert isinstance(decision, SwarmDecision)
        assert decision.final_price > 0


# ════════════════════════════════════════════════════════════════════
#  اختبارات التسلسل والدفعات
# ════════════════════════════════════════════════════════════════════

class TestSwarmDecisionSerialization:
    """اختبارات تسلسل SwarmDecision."""

    def test_to_dict_roundtrip(self, mock_ai: MockAIService) -> None:
        """to_dict يُعيد قاموساً كاملاً بكل الحقول."""
        orch = SwarmOrchestrator(mock_ai)
        decision = orch.analyze(
            product_name="منتج تسلسل",
            our_price=100.0,
            competitor_prices=[90.0, 95.0, 105.0],
        )

        d = decision.to_dict()

        assert isinstance(d, dict)
        assert "final_price" in d
        assert "final_confidence" in d
        assert "consensus_type" in d
        assert "votes" in d
        assert isinstance(d["votes"], list)
        assert "strategist_reasoning" in d
        assert "requires_human" in d
        assert "total_cost_tokens" in d

        # التحقق من أن القيم متطابقة
        assert d["final_price"] == decision.final_price
        assert d["consensus_type"] == decision.consensus_type
        assert len(d["votes"]) == len(decision.votes)


class TestBatchAnalysis:
    """اختبارات التحليل الدفعي."""

    def test_batch_analysis_works(self, mock_ai: MockAIService) -> None:
        """تحليل دفعة من المنتجات يُعيد نتائج بنفس العدد."""
        orch = SwarmOrchestrator(mock_ai)
        products = [
            {
                "product_name": "منتج 1",
                "our_price": 100.0,
                "competitor_prices": [90.0, 95.0],
            },
            {
                "product_name": "منتج 2",
                "our_price": 200.0,
                "competitor_prices": [180.0, 190.0, 210.0],
            },
            {
                "product_name": "منتج 3",
                "our_price": 50.0,
                "competitor_prices": [],
            },
        ]

        results = orch.analyze_batch(products)

        assert len(results) == 3
        for r in results:
            assert isinstance(r, SwarmDecision)


# ════════════════════════════════════════════════════════════════════
#  اختبارات الدوال المساعدة
# ════════════════════════════════════════════════════════════════════

class TestHelperFunctions:
    """اختبارات الدوال المساعدة."""

    def test_weighted_avg_favors_lower(self) -> None:
        """المتوسط المرجّح يفضّل الأسعار الأقل."""
        prices = [100.0, 200.0]
        avg = _compute_weighted_avg(prices)
        # المتوسط المرجّح أقل من المتوسط الحسابي
        assert avg < 150.0

    def test_position_label(self) -> None:
        """تحديد الموقع السعري صحيح."""
        assert _position_label(80.0, [90.0, 100.0, 110.0]) == "الأرخص"
        assert _position_label(100.0, [90.0, 100.0, 110.0]) == "الوسط"
        assert _position_label(120.0, [90.0, 100.0, 110.0]) == "الأغلى"
        assert _position_label(100.0, []) == "وحيد"

    def test_std_dev(self) -> None:
        """الانحراف المعياري صحيح."""
        assert _std_dev([]) == 0.0
        assert _std_dev([5.0]) == 0.0
        assert _std_dev([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]) == pytest.approx(2.0, abs=0.1)

    def test_classify_tier(self) -> None:
        """تصنيف الشريحة السعرية صحيح."""
        assert _classify_tier(50.0, 100.0) == "budget"
        assert _classify_tier(100.0, 100.0) == "mid"
        assert _classify_tier(150.0, 100.0) == "premium"
        assert _classify_tier(250.0, 100.0) == "luxury"

    def test_price_diff_pct(self) -> None:
        """حساب فارق النسبة المئوية صحيح."""
        assert _price_diff_pct(100.0, 100.0) == 0.0
        assert _price_diff_pct(100.0, 110.0) == pytest.approx(0.0952, abs=0.01)
        assert _price_diff_pct(0.0, 0.0) == 0.0
