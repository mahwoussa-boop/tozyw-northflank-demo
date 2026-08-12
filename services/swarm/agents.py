"""services/swarm/agents.py — وكلاء نظام السَّرب (Swarm) للتسعير الذكي.

ثلاثة وكلاء متخصصون يتعاونون على قرارات التسعير:
- **المحلل (Analyst)**: تحليل رياضي + AI اختياري لتحديد السعر الأمثل.
- **المدقق (Auditor)**: فحص قواعد الأمان والهوامش والقيم الشاذة.
- **الاستراتيجي (Strategist)**: تحكيم النزاعات بين المحلل والمدقق.

⚠️ لا يستورد Streamlit أبداً. كل وكيل يُحقن عبر المُنشئ (DI).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

from services.ai_service import AIService

logger = logging.getLogger("swarm.agents")


# ════════════════════════════════════════════════════════════════════
#  السياق المشترك
# ════════════════════════════════════════════════════════════════════

@dataclass
class SharedContext:
    """السياق المشترك الذي يتشاركه جميع الوكلاء لاتخاذ قرار تسعير واحد.

    يحتوي على بيانات المنتج الحالية وبيانات السوق والأنماط التاريخية.
    يُبنى مرة واحدة في المنسّق ويُمرَّر لكل وكيل.
    """

    product_name: str
    brand: str
    our_price: float
    competitor_prices: list[float] = field(default_factory=list)
    section: str = ""
    market_avg: float = 0.0
    market_min: float = 0.0
    market_max: float = 0.0
    price_tier: str = "mid"  # budget / mid / premium / luxury
    patterns: list[str] = field(default_factory=list)
    historical_accuracy: float = 0.5


# ════════════════════════════════════════════════════════════════════
#  تصويت الوكيل
# ════════════════════════════════════════════════════════════════════

@dataclass
class AgentVote:
    """تصويت وكيل واحد: السعر المقترح + الثقة + المبررات + التحذيرات.

    كل وكيل يُعيد تصويتاً واحداً يحتوي:
    - ``agent_name``: اسم الوكيل (analyst/auditor/strategist).
    - ``suggested_price``: السعر المقترح بالعملة.
    - ``confidence``: مستوى الثقة (0.0 - 1.0).
    - ``reasoning``: شرح بالعربية لسبب الاقتراح.
    - ``flags``: تحذيرات مثل ["below_margin", "outlier_detected"].
    - ``cost_tokens``: عدد الرموز المُستهلكة (لتتبع التكلفة).
    """

    agent_name: str
    suggested_price: float
    confidence: float
    reasoning: str
    flags: list[str] = field(default_factory=list)
    cost_tokens: int = 0


# ════════════════════════════════════════════════════════════════════
#  دوال مساعدة
# ════════════════════════════════════════════════════════════════════

def _compute_weighted_avg(prices: list[float]) -> float:
    """متوسط مرجّح يُعطي وزناً أعلى للأسعار الأقل (تنافسية).

    الأوزان = 1/price (معيارة). الأسعار الأقل تحصل على وزن أكبر
    لأن المستهلك يميل للسعر الأرخص.
    """
    if not prices:
        return 0.0
    # أوزان عكسية: سعر أقل = وزن أعلى
    weights = [1.0 / p for p in prices if p > 0]
    if not weights:
        return sum(prices) / len(prices)
    total_weight = sum(weights)
    weighted = sum(w * p for w, p in zip(weights, [p for p in prices if p > 0]))
    return weighted / total_weight


def _position_label(our_price: float, prices: list[float]) -> str:
    """يحدد موقع سعرنا بين المنافسين: الأرخص / الوسط / الأغلى."""
    if not prices:
        return "وحيد"
    sorted_prices = sorted(prices)
    if our_price <= sorted_prices[0]:
        return "الأرخص"
    if our_price >= sorted_prices[-1]:
        return "الأغلى"
    return "الوسط"


def _std_dev(values: list[float]) -> float:
    """الانحراف المعياري للقيم."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    return math.sqrt(variance)


# ════════════════════════════════════════════════════════════════════
#  وكيل المحلل (Analyst)
# ════════════════════════════════════════════════════════════════════

class AnalystAgent:
    """وكيل المحلل — يحسب السعر الأمثل من بيانات السوق.

    يعمل دائماً (إلزامي في كل تحليل). يبدأ بتحليل رياضي بحت
    ثم يستدعي AI اختيارياً لإثراء التبرير. إذا فشل AI، يعتمد
    على النتيجة الرياضية فقط.
    """

    def __init__(self, ai: AIService) -> None:
        """يستقبل خدمة AI للاستدعاء الاختياري."""
        self._ai = ai

    def vote(self, ctx: SharedContext) -> AgentVote:
        """يحلل بيانات السوق ويقترح سعراً أمثل.

        الخطوات:
        1. إذا لا يوجد منافسون → يحتفظ بالسعر الحالي (ثقة 0.3).
        2. يحسب المتوسط المرجّح المفضّل للأسعار الأقل.
        3. يحلل موقعنا السعري بين المنافسين.
        4. (اختياري) يستدعي AI لتبرير إضافي.
        5. يُعيد AgentVote باسم 'analyst'.
        """
        flags: list[str] = []

        # ── حالة خاصة: لا يوجد منافسون ──
        if not ctx.competitor_prices:
            return AgentVote(
                agent_name="analyst",
                suggested_price=ctx.our_price,
                confidence=0.3,
                reasoning="لا تتوفر أسعار منافسين — يُحتفظ بالسعر الحالي بثقة منخفضة.",
                flags=["no_competitors"],
            )

        # ── التحليل الرياضي ──
        valid_prices = [p for p in ctx.competitor_prices if p > 0]
        if not valid_prices:
            return AgentVote(
                agent_name="analyst",
                suggested_price=ctx.our_price,
                confidence=0.3,
                reasoning="أسعار المنافسين غير صالحة — يُحتفظ بالسعر الحالي.",
                flags=["invalid_competitor_prices"],
            )

        weighted_avg = _compute_weighted_avg(valid_prices)
        position = _position_label(ctx.our_price, valid_prices)
        market_avg = ctx.market_avg if ctx.market_avg > 0 else sum(valid_prices) / len(valid_prices)
        market_min = ctx.market_min if ctx.market_min > 0 else min(valid_prices)
        market_max = ctx.market_max if ctx.market_max > 0 else max(valid_prices)

        # ── السعر المقترح: المتوسط المرجّح مع تعديل حسب الموقع ──
        suggested = weighted_avg
        if position == "الأرخص":
            # نحن الأرخص — يمكن رفع السعر قليلاً نحو المتوسط
            suggested = ctx.our_price + (weighted_avg - ctx.our_price) * 0.3
        elif position == "الأغلى":
            # نحن الأغلى — نتحرك نحو المتوسط المرجّح
            suggested = weighted_avg
            flags.append("price_above_market")

        # ── حساب الثقة ──
        n_competitors = len(valid_prices)
        spread = (market_max - market_min) / market_avg if market_avg > 0 else 1.0
        # ثقة أعلى: منافسون أكثر + انتشار أقل
        base_confidence = min(0.5 + n_competitors * 0.1, 0.9)
        spread_penalty = min(spread * 0.3, 0.3)
        confidence = max(0.2, base_confidence - spread_penalty)

        # تعديل الثقة حسب الدقة التاريخية
        confidence = confidence * 0.7 + ctx.historical_accuracy * 0.3

        # تقريب السعر المقترح
        suggested = round(suggested, 2)

        # ── بناء التبرير ──
        reasoning = (
            f"تحليل رياضي: المتوسط المرجّح للسوق {weighted_avg:.2f}، "
            f"موقعنا: {position}، "
            f"عدد المنافسين: {n_competitors}، "
            f"نطاق السوق: {market_min:.2f} - {market_max:.2f}."
        )

        # ── استدعاء AI اختياري للتبرير ──
        ai_tokens = 0
        try:
            prompt = (
                f"أنت محلل تسعير. المنتج: {ctx.product_name}. "
                f"سعرنا: {ctx.our_price}. أسعار المنافسين: {valid_prices}. "
                f"المتوسط المرجّح: {weighted_avg:.2f}. موقعنا: {position}. "
                f"السعر المقترح رياضياً: {suggested}. "
                f"اشرح بجملة واحدة بالعربية لماذا هذا السعر مناسب."
            )
            result = self._ai.call(prompt, system="أنت محلل تسعير خبير. أجب بجملة واحدة بالعربية.")
            if result.success and result.response.strip():
                reasoning += f" | تحليل AI: {result.response.strip()}"
                ai_tokens = len(result.response) // 4  # تقدير تقريبي
        except Exception as exc:
            logger.debug("فشل استدعاء AI للمحلل: %s — يُستخدم التحليل الرياضي فقط", exc)

        return AgentVote(
            agent_name="analyst",
            suggested_price=suggested,
            confidence=round(confidence, 3),
            reasoning=reasoning,
            flags=flags,
            cost_tokens=ai_tokens,
        )


# ════════════════════════════════════════════════════════════════════
#  وكيل المدقق (Auditor)
# ════════════════════════════════════════════════════════════════════

class AuditorAgent:
    """وكيل المدقق — يفحص اقتراح المحلل ضد قواعد الأمان.

    يتدخل فقط عند انخفاض ثقة المحلل (< 85%). يفحص:
    - حد الانخفاض الأقصى: لا يُسمح بخفض السعر أكثر من 15% دفعة واحدة.
    - أرضية الهامش: السعر لا يقل عن التكلفة + هامش أدنى.
    - فحص القيم الشاذة: إذا كان السعر المقترح > 2σ عن المتوسط.
    """

    # ثوابت قواعد الأمان
    MAX_DROP_PCT: float = 0.15       # أقصى انخفاض مسموح: 15%
    MIN_MARGIN_PCT: float = 0.05     # هامش أدنى: 5% فوق التكلفة
    OUTLIER_SIGMA: float = 2.0       # عتبة الشذوذ: 2 انحرافات معيارية

    def __init__(self, ai: AIService) -> None:
        """يستقبل خدمة AI (تُستخدم لتعزيز التبرير فقط)."""
        self._ai = ai

    def vote(self, ctx: SharedContext, analyst_vote: AgentVote) -> AgentVote:
        """يراجع اقتراح المحلل ضد قواعد الأمان.

        يعدّل السعر إذا انتُهكت القواعد، ويضيف تحذيرات.
        يُعيد AgentVote باسم 'auditor'.

        المعاملات:
            ctx: السياق المشترك.
            analyst_vote: تصويت المحلل المُراد مراجعته.
        """
        suggested = analyst_vote.suggested_price
        flags: list[str] = list(analyst_vote.flags)  # نسخة من تحذيرات المحلل
        adjustments: list[str] = []
        adjusted = False

        # ── فحص 1: حد الانخفاض الأقصى (15%) ──
        if ctx.our_price > 0:
            drop_pct = (ctx.our_price - suggested) / ctx.our_price
            if drop_pct > self.MAX_DROP_PCT:
                old_suggested = suggested
                suggested = ctx.our_price * (1 - self.MAX_DROP_PCT)
                suggested = round(suggested, 2)
                flags.append("max_drop_exceeded")
                adjustments.append(
                    f"تجاوز حد الانخفاض ({drop_pct:.1%} > {self.MAX_DROP_PCT:.0%}): "
                    f"عُدّل من {old_suggested:.2f} إلى {suggested:.2f}"
                )
                adjusted = True

        # ── فحص 2: أرضية الهامش ──
        # نستخدم market_min كتقدير للتكلفة إذا لم يُحدد
        cost_estimate = ctx.market_min * 0.7 if ctx.market_min > 0 else 0.0
        if cost_estimate > 0:
            margin_floor = cost_estimate * (1 + self.MIN_MARGIN_PCT)
            if suggested < margin_floor:
                old_suggested = suggested
                suggested = round(margin_floor, 2)
                flags.append("below_margin")
                adjustments.append(
                    f"السعر أقل من أرضية الهامش ({margin_floor:.2f}): "
                    f"عُدّل من {old_suggested:.2f} إلى {suggested:.2f}"
                )
                adjusted = True

        # ── فحص 3: القيم الشاذة ──
        if ctx.competitor_prices:
            all_prices = list(ctx.competitor_prices)
            std = _std_dev(all_prices)
            mean = sum(all_prices) / len(all_prices)
            if std > 0 and abs(suggested - mean) > self.OUTLIER_SIGMA * std:
                flags.append("outlier_detected")
                # نقرّب السعر نحو المتوسط
                direction = 1 if suggested > mean else -1
                old_suggested = suggested
                suggested = round(mean + direction * self.OUTLIER_SIGMA * std, 2)
                adjustments.append(
                    f"السعر شاذ (>{self.OUTLIER_SIGMA}σ عن المتوسط {mean:.2f}): "
                    f"عُدّل من {old_suggested:.2f} إلى {suggested:.2f}"
                )
                adjusted = True

        # ── حساب الثقة ──
        # المدقق واثق إذا لم يُعدّل شيئاً
        confidence = 0.9 if not adjusted else 0.65

        # ── بناء التبرير ──
        if adjustments:
            reasoning = "تعديلات المدقق: " + " | ".join(adjustments)
        else:
            reasoning = "اجتاز اقتراح المحلل جميع فحوصات الأمان بنجاح."

        return AgentVote(
            agent_name="auditor",
            suggested_price=suggested,
            confidence=round(confidence, 3),
            reasoning=reasoning,
            flags=flags,
            cost_tokens=0,  # المدقق لا يستدعي AI عادةً
        )


# ════════════════════════════════════════════════════════════════════
#  وكيل الاستراتيجي (Strategist)
# ════════════════════════════════════════════════════════════════════

class StrategistAgent:
    """وكيل الاستراتيجي — يحكّم النزاعات بين المحلل والمدقق.

    يُستدعى فقط عند وجود خلاف (فارق > 3% بين المحلل والمدقق).
    يأخذ بعين الاعتبار: التصويتات، الأنماط، والسياق الاستراتيجي.
    هذا الوكيل مُكلف (يستدعي AI) — لذا يُستخدم بحرص.
    """

    def __init__(self, ai: AIService) -> None:
        """يستقبل خدمة AI للتحكيم."""
        self._ai = ai

    def arbitrate(self, ctx: SharedContext, votes: list[AgentVote]) -> AgentVote:
        """يحكّم بين تصويتات الوكلاء المتنازعين ويصدر قراراً نهائياً.

        المعاملات:
            ctx: السياق المشترك.
            votes: قائمة تصويتات المحلل والمدقق.

        الخطوات:
        1. يحلل الفجوة بين التصويتات.
        2. يطبّق قواعد تحكيم رياضية.
        3. (اختياري) يستدعي AI للقرار النهائي.
        4. يُعيد AgentVote باسم 'strategist'.
        """
        if len(votes) < 2:
            # حالة حافة: تصويت واحد فقط
            single = votes[0] if votes else AgentVote(
                agent_name="strategist",
                suggested_price=ctx.our_price,
                confidence=0.3,
                reasoning="لا تتوفر تصويتات كافية للتحكيم.",
            )
            return AgentVote(
                agent_name="strategist",
                suggested_price=single.suggested_price,
                confidence=single.confidence * 0.9,
                reasoning=f"تحكيم: تصويت واحد فقط من {single.agent_name}.",
                flags=list(single.flags),
                cost_tokens=0,
            )

        analyst_vote = votes[0]
        auditor_vote = votes[1]

        # ── حساب الفجوة ──
        avg_price = (analyst_vote.suggested_price + auditor_vote.suggested_price) / 2
        if avg_price > 0:
            gap_pct = abs(analyst_vote.suggested_price - auditor_vote.suggested_price) / avg_price
        else:
            gap_pct = 0.0

        # ── التحكيم الرياضي: متوسط مرجّح بالثقة ──
        total_conf = analyst_vote.confidence + auditor_vote.confidence
        if total_conf > 0:
            w_analyst = analyst_vote.confidence / total_conf
            w_auditor = auditor_vote.confidence / total_conf
        else:
            w_analyst = w_auditor = 0.5

        suggested = round(
            analyst_vote.suggested_price * w_analyst + auditor_vote.suggested_price * w_auditor,
            2,
        )

        # ── تجميع التحذيرات ──
        all_flags = list(set(analyst_vote.flags + auditor_vote.flags))

        # ── الثقة: أقل من أعلى تصويت ──
        confidence = max(analyst_vote.confidence, auditor_vote.confidence) * 0.85

        # ── بناء التبرير ──
        reasoning = (
            f"تحكيم الاستراتيجي: فجوة {gap_pct:.1%} بين المحلل ({analyst_vote.suggested_price:.2f}) "
            f"والمدقق ({auditor_vote.suggested_price:.2f}). "
            f"القرار: متوسط مرجّح بالثقة = {suggested:.2f} "
            f"(وزن المحلل {w_analyst:.0%}، وزن المدقق {w_auditor:.0%})."
        )

        # ── استدعاء AI اختياري ──
        ai_tokens = 0
        try:
            prompt = (
                f"أنت استراتيجي تسعير. المنتج: {ctx.product_name}. "
                f"المحلل يقترح {analyst_vote.suggested_price:.2f} (ثقة {analyst_vote.confidence:.0%}). "
                f"المدقق يقترح {auditor_vote.suggested_price:.2f} (ثقة {auditor_vote.confidence:.0%}). "
                f"الفجوة: {gap_pct:.1%}. التحذيرات: {all_flags}. "
                f"ما القرار النهائي؟ أجب بجملة واحدة بالعربية."
            )
            result = self._ai.call(prompt, system="أنت استراتيجي تسعير خبير. أجب بجملة بالعربية.")
            if result.success and result.response.strip():
                reasoning += f" | رؤية AI: {result.response.strip()}"
                ai_tokens = len(result.response) // 4
        except Exception as exc:
            logger.debug("فشل استدعاء AI للاستراتيجي: %s", exc)

        return AgentVote(
            agent_name="strategist",
            suggested_price=suggested,
            confidence=round(confidence, 3),
            reasoning=reasoning,
            flags=all_flags,
            cost_tokens=ai_tokens,
        )
