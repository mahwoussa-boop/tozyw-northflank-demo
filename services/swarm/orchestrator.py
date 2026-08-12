"""services/swarm/orchestrator.py — منسّق السَّرب (Swarm Orchestrator).

ينسّق تشغيل الوكلاء الثلاثة وفق قواعد تحسين التكلفة:
1. المحلل يعمل **دائماً** (إلزامي).
2. المدقق يعمل **فقط** إذا ثقة المحلل < 85%.
3. الاستراتيجي يعمل **فقط** عند خلاف > 3% بين المحلل والمدقق.

يدعم التشغيل بدون معايِر الثقة وذاكرة الأنماط (تدهور سلس).

⚠️ لا يستورد Streamlit أبداً.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from services.ai_service import AIService
from services.swarm.agents import (
    AgentVote,
    AnalystAgent,
    AuditorAgent,
    SharedContext,
    StrategistAgent,
)

logger = logging.getLogger("swarm.orchestrator")


# ════════════════════════════════════════════════════════════════════
#  بروتوكولات اختيارية (التعلم والمعايرة)
# ════════════════════════════════════════════════════════════════════

class ConfidenceCalibrator(Protocol):
    """بروتوكول معايِر الثقة — يُعدّل ثقة الوكيل بناءً على الأداء التاريخي."""

    def calibrate(self, raw_confidence: float, context: dict[str, Any]) -> float:
        """يُعيد الثقة المعايَرة (0.0 - 1.0)."""
        ...


class PatternMemory(Protocol):
    """بروتوكول ذاكرة الأنماط — يستخرج الأنماط المطبّقة من التاريخ."""

    def get_patterns(self, product_name: str, brand: str, section: str) -> list[str]:
        """يُعيد قائمة الأنماط النصية المطبّقة."""
        ...


# ════════════════════════════════════════════════════════════════════
#  قرار السَّرب
# ════════════════════════════════════════════════════════════════════

@dataclass
class SwarmDecision:
    """قرار السَّرب النهائي: السعر + الثقة + نوع الإجماع + التصويتات.

    أنواع الإجماع:
    - ``unanimous``: إجماع — المحلل واثق أو المحلل والمدقق متفقان.
    - ``majority``: أغلبية — اتفاق قريب (فارق < 3%).
    - ``arbitrated``: تحكيم — الاستراتيجي تدخّل (فارق 3-15%).
    - ``escalated``: تصعيد — خلاف كبير (> 15%) يحتاج بشري.
    """

    final_price: float
    final_confidence: float
    consensus_type: str  # unanimous | majority | arbitrated | escalated
    votes: list[AgentVote] = field(default_factory=list)
    strategist_reasoning: str = ""
    requires_human: bool = False
    total_cost_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        """تحويل القرار لقاموس للتخزين والتسلسل.

        يحوّل كل التصويتات والحقول لقاموس JSON-serializable.
        """
        return {
            "final_price": self.final_price,
            "final_confidence": self.final_confidence,
            "consensus_type": self.consensus_type,
            "votes": [
                {
                    "agent_name": v.agent_name,
                    "suggested_price": v.suggested_price,
                    "confidence": v.confidence,
                    "reasoning": v.reasoning,
                    "flags": list(v.flags),
                    "cost_tokens": v.cost_tokens,
                }
                for v in self.votes
            ],
            "strategist_reasoning": self.strategist_reasoning,
            "requires_human": self.requires_human,
            "total_cost_tokens": self.total_cost_tokens,
        }


# ════════════════════════════════════════════════════════════════════
#  دوال مساعدة
# ════════════════════════════════════════════════════════════════════

def _classify_tier(our_price: float, market_avg: float) -> str:
    """يصنّف شريحة السعر: budget / mid / premium / luxury."""
    if market_avg <= 0 or our_price <= 0:
        return "mid"
    ratio = our_price / market_avg
    if ratio < 0.7:
        return "budget"
    if ratio < 1.3:
        return "mid"
    if ratio < 2.0:
        return "premium"
    return "luxury"


def _price_diff_pct(price_a: float, price_b: float) -> float:
    """النسبة المئوية للفارق بين سعرين (مطلقة)."""
    avg = (price_a + price_b) / 2
    if avg <= 0:
        return 0.0
    return abs(price_a - price_b) / avg


# ════════════════════════════════════════════════════════════════════
#  المنسّق
# ════════════════════════════════════════════════════════════════════

class SwarmOrchestrator:
    """منسّق السَّرب — يدير دورة التحليل الكاملة.

    يحقن خدمة AI ويقبل اختيارياً معايِر الثقة وذاكرة الأنماط.
    يعمل بدونهما بشكل سليم (تدهور سلس).

    قواعد تحسين التكلفة:
    - المحلل: دائماً (إلزامي).
    - المدقق: فقط إذا ثقة المحلل المعايَرة < 85%.
    - الاستراتيجي: فقط عند خلاف > 3%.
    """

    # عتبات التشغيل
    CONFIDENCE_THRESHOLD: float = 0.85  # عتبة تجاوز المدقق
    CONFLICT_THRESHOLD: float = 0.03   # عتبة استدعاء الاستراتيجي (3%)
    ESCALATION_THRESHOLD: float = 0.15  # عتبة التصعيد للبشري (15%)

    def __init__(
        self,
        ai: AIService,
        calibrator: Optional[ConfidenceCalibrator] = None,
        memory: Optional[PatternMemory] = None,
    ) -> None:
        """يبني المنسّق مع الاعتماديات.

        المعاملات:
            ai: خدمة AI (إلزامية).
            calibrator: معايِر الثقة (اختياري — تدهور سلس).
            memory: ذاكرة الأنماط (اختيارية — تدهور سلس).
        """
        self._ai = ai
        self._calibrator = calibrator
        self._memory = memory
        self._analyst = AnalystAgent(ai)
        self._auditor = AuditorAgent(ai)
        self._strategist = StrategistAgent(ai)

    def _build_context(
        self,
        *,
        product_name: str,
        brand: str,
        our_price: float,
        competitor_prices: list[float],
        section: str,
    ) -> SharedContext:
        """يبني السياق المشترك من المدخلات.

        يحسب إحصائيات السوق ويصنّف الشريحة السعرية ويستعلم الأنماط.
        """
        valid_prices = [p for p in competitor_prices if p > 0]
        market_avg = sum(valid_prices) / len(valid_prices) if valid_prices else 0.0
        market_min = min(valid_prices) if valid_prices else 0.0
        market_max = max(valid_prices) if valid_prices else 0.0
        price_tier = _classify_tier(our_price, market_avg)

        # استعلام الأنماط من الذاكرة (اختياري)
        patterns: list[str] = []
        if self._memory is not None:
            try:
                patterns = self._memory.get_patterns(product_name, brand, section)
            except Exception as exc:
                logger.debug("فشل استعلام الأنماط: %s", exc)

        # الدقة التاريخية (يُمكن تحسينها لاحقاً عبر ConfidenceCalibrator)
        historical_accuracy = 0.5

        return SharedContext(
            product_name=product_name,
            brand=brand,
            our_price=our_price,
            competitor_prices=list(competitor_prices),
            section=section,
            market_avg=round(market_avg, 2),
            market_min=round(market_min, 2),
            market_max=round(market_max, 2),
            price_tier=price_tier,
            patterns=patterns,
            historical_accuracy=historical_accuracy,
        )

    def _calibrate_confidence(self, raw: float, ctx: SharedContext) -> float:
        """يعايِر الثقة عبر المعايِر إن وُجد، وإلا يُعيد الثقة الخام."""
        if self._calibrator is None:
            return raw
        try:
            return self._calibrator.calibrate(raw, {
                "product_name": ctx.product_name,
                "brand": ctx.brand,
                "section": ctx.section,
                "price_tier": ctx.price_tier,
            })
        except Exception as exc:
            logger.debug("فشل معايرة الثقة: %s — تُستخدم الثقة الخام", exc)
            return raw

    def analyze(
        self,
        *,
        product_name: str,
        brand: str = "",
        our_price: float,
        competitor_prices: list[float],
        section: str = "",
        cost: float = 0.0,
    ) -> SwarmDecision:
        """يشغّل دورة التحليل الكاملة ويُعيد قرار السَّرب.

        الخطوات:
        1. بناء السياق المشترك.
        2. تشغيل المحلل (إلزامي).
        3. معايرة الثقة → إذا ≥ 85%: إجماع فوري.
        4. تشغيل المدقق (إذا ثقة < 85%).
        5. مقارنة المحلل والمدقق:
           - فارق < 3%: إجماع.
           - فارق 3-15%: تحكيم الاستراتيجي.
           - فارق > 15%: تصعيد للبشري.
        6. معايرة الثقة النهائية.
        7. إعادة SwarmDecision.

        المعاملات:
            product_name: اسم المنتج.
            brand: العلامة التجارية (اختياري).
            our_price: سعرنا الحالي.
            competitor_prices: أسعار المنافسين.
            section: القسم (اختياري).
            cost: التكلفة (اختياري — للتحقق من الهوامش).
        """
        # ── الخطوة 1: بناء السياق ──
        ctx = self._build_context(
            product_name=product_name,
            brand=brand,
            our_price=our_price,
            competitor_prices=competitor_prices,
            section=section,
        )

        # ── الخطوة 2: تشغيل المحلل (دائماً) ──
        analyst_vote = self._analyst.vote(ctx)
        all_votes: list[AgentVote] = [analyst_vote]
        total_tokens = analyst_vote.cost_tokens

        # ── الخطوة 3: معايرة ثقة المحلل ──
        calibrated_confidence = self._calibrate_confidence(analyst_vote.confidence, ctx)

        # ── إذا الثقة المعايَرة ≥ 85%: إجماع فوري (توفير تكلفة) ──
        if calibrated_confidence >= self.CONFIDENCE_THRESHOLD:
            logger.debug(
                "ثقة المحلل المعايَرة %.1f%% ≥ 85%% — تجاوز المدقق والاستراتيجي",
                calibrated_confidence * 100,
            )
            return SwarmDecision(
                final_price=analyst_vote.suggested_price,
                final_confidence=round(calibrated_confidence, 3),
                consensus_type="unanimous",
                votes=all_votes,
                strategist_reasoning="",
                requires_human=False,
                total_cost_tokens=total_tokens,
            )

        # ── الخطوة 4: تشغيل المدقق ──
        auditor_vote = self._auditor.vote(ctx, analyst_vote)
        all_votes.append(auditor_vote)
        total_tokens += auditor_vote.cost_tokens

        # ── الخطوة 5: مقارنة المحلل والمدقق ──
        diff_pct = _price_diff_pct(analyst_vote.suggested_price, auditor_vote.suggested_price)

        if diff_pct <= self.CONFLICT_THRESHOLD:
            # ── فارق < 3%: إجماع — نستخدم سعر المحلل ──
            final_confidence = self._calibrate_confidence(
                max(analyst_vote.confidence, auditor_vote.confidence), ctx,
            )
            return SwarmDecision(
                final_price=analyst_vote.suggested_price,
                final_confidence=round(final_confidence, 3),
                consensus_type="unanimous",
                votes=all_votes,
                strategist_reasoning="",
                requires_human=False,
                total_cost_tokens=total_tokens,
            )

        if diff_pct > self.ESCALATION_THRESHOLD:
            # ── فارق > 15%: تصعيد للبشري ──
            # نستخدم سعر المدقق (أكثر أماناً)
            final_confidence = self._calibrate_confidence(
                min(analyst_vote.confidence, auditor_vote.confidence) * 0.5, ctx,
            )
            return SwarmDecision(
                final_price=auditor_vote.suggested_price,
                final_confidence=round(final_confidence, 3),
                consensus_type="escalated",
                votes=all_votes,
                strategist_reasoning=f"تصعيد: فجوة {diff_pct:.1%} > {self.ESCALATION_THRESHOLD:.0%} — يحتاج مراجعة بشرية.",
                requires_human=True,
                total_cost_tokens=total_tokens,
            )

        # ── فارق 3-15%: تحكيم الاستراتيجي ──
        strategist_vote = self._strategist.arbitrate(ctx, all_votes)
        all_votes.append(strategist_vote)
        total_tokens += strategist_vote.cost_tokens

        final_confidence = self._calibrate_confidence(strategist_vote.confidence, ctx)

        return SwarmDecision(
            final_price=strategist_vote.suggested_price,
            final_confidence=round(final_confidence, 3),
            consensus_type="arbitrated",
            votes=all_votes,
            strategist_reasoning=strategist_vote.reasoning,
            requires_human=False,
            total_cost_tokens=total_tokens,
        )

    def analyze_batch(
        self,
        products: list[dict[str, Any]],
    ) -> list[SwarmDecision]:
        """تحليل دفعة من المنتجات مع تتبع التقدم.

        كل عنصر في القائمة قاموس بمفاتيح:
        - product_name (إلزامي)
        - our_price (إلزامي)
        - competitor_prices (إلزامي)
        - brand, section, cost (اختيارية)

        يُعيد قائمة SwarmDecision بنفس ترتيب المدخلات.
        """
        results: list[SwarmDecision] = []
        total = len(products)
        for i, product in enumerate(products):
            logger.info("تحليل السَّرب: المنتج %d/%d — %s", i + 1, total, product.get("product_name", ""))
            try:
                decision = self.analyze(
                    product_name=product.get("product_name", ""),
                    brand=product.get("brand", ""),
                    our_price=float(product.get("our_price", 0)),
                    competitor_prices=list(product.get("competitor_prices", [])),
                    section=product.get("section", ""),
                    cost=float(product.get("cost", 0)),
                )
                results.append(decision)
            except Exception as exc:
                logger.error("فشل تحليل المنتج %s: %s", product.get("product_name", ""), exc)
                results.append(SwarmDecision(
                    final_price=float(product.get("our_price", 0)),
                    final_confidence=0.0,
                    consensus_type="error",
                    votes=[],
                    strategist_reasoning=f"خطأ: {exc}",
                    requires_human=True,
                    total_cost_tokens=0,
                ))
        return results
