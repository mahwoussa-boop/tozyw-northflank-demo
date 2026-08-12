"""services/copilot_service.py — العقل المدبر لمساعد التسعير الذكي (AI Copilot).

ينسّق بين:
- ``AIService`` (سلسلة التجاوز: OpenRouter→Gemini→Cohere)
- ``AIDecisionRepository`` (ذاكرة التعلّم + تسجيل القرارات)
- القواعد الذهبية الثلاث (Guardrails) من ``ai_decisions_repo``

═══════════════════════════════════════════════════════════════════
  التدفق الكامل لتحليل دفعة منتجات
═══════════════════════════════════════════════════════════════════
1. يجمع المنتجات في دفعات ≤15 منتج/طلب
2. يجلب أمثلة التعلّم (RAG) من قرارات المستخدم السابقة
3. يبني System Prompt يلزم AI بالقواعد الذهبية الثلاث
4. يرسل الدفعة لـ AI ويتوقع JSON صارم
5. يطبّق الحراسات البرمجية (Guardrails) على كل سعر مقترح
6. يسجّل القرارات في قاعدة البيانات بحالة ``pending``
7. يُعيد القرارات للواجهة لعرضها على المستخدم

⚠️ فصل تام: لا Streamlit هنا. لا SQL مباشر. كل شيء محقون.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol, Sequence

from infrastructure.ai_decisions_repo import (
    AIDecision,
    AIDecisionRepository,
    MarketContext,
    SafetyResult,
    compute_market_context,
    validate_price_safety,
)
from services.ai_service import AIResult, AIService, parse_json
from services.learning.feedback_collector import FeedbackCollector

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════
#  ثوابت التهيئة
# ════════════════════════════════════════════════════════════════════
DEFAULT_BATCH_SIZE: int = 15        # 15 منتج/طلب (معتمد من المستخدم)
DEFAULT_MAX_ITEMS: int = 150        # حد أقصى لكل تحليل
DEFAULT_MAX_DROP_PCT: float = 0.15  # القاعدة 2: 15% حد أقصى للهبوط
DEFAULT_OUTLIER_PCT: float = 0.30   # القاعدة 1: 30% عتبة الشذوذ


# ════════════════════════════════════════════════════════════════════
#  نموذج المنتج المدخل (Product Input)
# ════════════════════════════════════════════════════════════════════
@dataclass
class ProductInput:
    """منتج مدخل واحد للتحليل السعري بالـ AI."""

    name: str
    product_id: str = ""
    our_price: float = 0.0
    competitor_prices: list[float] = field(default_factory=list)
    competitor_names: list[str] = field(default_factory=list)
    section: str = ""  # price_raise / price_lower / missing


@dataclass
class CopilotSuggestion:
    """اقتراح AI واحد بعد تطبيق الحراسات (جاهز للعرض)."""

    decision: AIDecision
    safety: SafetyResult
    market: MarketContext
    db_id: int = 0  # معرف الصف في قاعدة البيانات


# ════════════════════════════════════════════════════════════════════
#  بناء الـ Prompts (System + User)
# ════════════════════════════════════════════════════════════════════

_SYSTEM_PROMPT = """\
أنت مساعد تسعير ذكي متخصص في العطور. مهمتك تحليل أسعار المنافسين واقتراح السعر الأمثل لمتجرنا.

═══ القواعد الذهبية الثلاث (يجب احترامها دائماً) ═══

1. كشف الشذوذ السعري (Outlier Detection):
   - إذا كان سعر أرخص منافس أقل من "متوسط سعر السوق" بـ30% أو أكثر، فهذا سعر شاذ (محروق).
   - لا تطابقه! اقترح سعراً قريباً من المتوسط.
   - اكتب في السبب: "تجاهلت المنافس X لأن سعره شاذ مقارنة بباقي السوق".

2. حاجز الهبوط الأقصى (Max Drop 15%):
   - لا تقترح أي سعر يقل عن سعرنا الحالي بأكثر من 15%.
   - إذا كان النزول المطلوب أكبر من 15%، اكتب decision="review" وفي السبب: "يتطلب نزولاً حاداً، يرجى المراجعة اليدوية".

3. إجماع السوق (Market Consensus):
   - إذا كان 3+ منافسين يبيعون بنفس السعر المنخفض (±3%) → هذا السعر الحقيقي الجديد. ثق به واقترحه.
   - إذا كان منافس واحد بسعر منخفض جداً والباقي مرتفعون → ارفض مجاراة المنافس الوحيد.

═══ صيغة الرد ═══
أعِد JSON array فقط بلا أي نص آخر. لكل منتج:
[
  {
    "i": 0,
    "decision": "lower",
    "suggested_price": 285,
    "reason": "المنافسون يبيعون بـ280-290 (متوسط 285) → نقترح مطابقة السوق",
    "confidence": 0.88
  }
]

decision: إحدى "raise" | "lower" | "hold" | "add" | "review"
suggested_price: رقم (ريال سعودي)
reason: جملة عربية مختصرة تشرح المنطق
confidence: رقم بين 0.0 و 1.0
"""


def build_rag_section(examples: list[dict[str, Any]]) -> str:
    """يبني قسم الأمثلة السابقة (RAG) من قرارات المستخدم المحسومة.

    يحوّل كل قرار سابق إلى سطر يُحقن في الـ prompt ليتعلّم AI من تفضيلات المستخدم.
    """
    if not examples:
        return ""

    lines = [
        "",
        "═══ أمثلة من قرارات المستخدم السابقة (تعلّم منها) ═══",
    ]
    for ex in examples:
        action = ex.get("user_action", "")
        name = ex.get("product_name", "")
        our = ex.get("our_price", 0)
        proposed = ex.get("proposed_price", 0)
        user_p = ex.get("user_price", 0)
        ai_dec = ex.get("ai_decision", "")
        flag = ex.get("safety_flag", "")

        if action == "approved":
            lines.append(
                f"  ✅ «{name}» سعرنا={our} → AI اقترح {proposed} ({ai_dec}) → المستخدم وافق"
            )
        elif action == "modified":
            lines.append(
                f"  ✏️ «{name}» سعرنا={our} → AI اقترح {proposed} ({ai_dec}) → المستخدم عدّل إلى {user_p}"
            )
        elif action == "rejected":
            reason = ex.get("ai_reason", "")[:50]
            lines.append(
                f"  ❌ «{name}» سعرنا={our} → AI اقترح {proposed} ({ai_dec}) → المستخدم رفض"
                + (f" ({reason})" if reason else "")
            )

        if flag:
            lines.append(f"     ⚠️ علم أمان: {flag}")

    lines.append("")
    return "\n".join(lines)


def build_similar_section(similar: list[dict[str, Any]]) -> str:
    """يبني قسم القرارات المشابهة (RAG سياقي) لمنتج محدد."""
    if not similar:
        return ""
    lines = ["  📋 قرارات سابقة لمنتجات مشابهة:"]
    for s in similar:
        action_icon = {"approved": "✅", "rejected": "❌", "modified": "✏️"}.get(
            s.get("user_action", ""), "❓"
        )
        lines.append(
            f"    {action_icon} سعرنا={s.get('our_price', 0)} → "
            f"اقتُرح {s.get('proposed_price', 0)} → "
            f"النتيجة: {s.get('user_action', '')}"
            + (f" (عدّل إلى {s.get('user_price', 0)})" if s.get("user_action") == "modified" else "")
        )
    return "\n".join(lines)


def build_batch_prompt(
    products: list[ProductInput],
    rag_examples: list[dict[str, Any]],
    similar_map: dict[str, list[dict[str, Any]]],
) -> str:
    """يبني prompt الدفعة (≤15 منتج) مع RAG + سياق سوقي لكل منتج.

    Parameters
    ----------
    products : list[ProductInput]
        المنتجات المطلوب تسعيرها.
    rag_examples : list[dict]
        أمثلة تعلّم عامة من قرارات المستخدم السابقة.
    similar_map : dict[str, list[dict]]
        قرارات سابقة مشابهة لكل منتج (بالاسم).
    """
    lines = ["حلّل المنتجات التالية واقترح السعر الأمثل لكل منها:"]

    # حقن RAG عام
    rag_section = build_rag_section(rag_examples)
    if rag_section:
        lines.append(rag_section)

    lines.append("═══ المنتجات المطلوب تحليلها ═══")
    lines.append("")

    for i, product in enumerate(products):
        ctx = compute_market_context(product.competitor_prices)
        lines.append(f"[{i}] المنتج: «{product.name}»")
        lines.append(f"    سعرنا الحالي: {product.our_price} ر.س")

        if ctx.competitor_count > 0:
            lines.append(f"    عدد المنافسين: {ctx.competitor_count}")
            lines.append(f"    أسعار المنافسين: {ctx.prices}")
            lines.append(f"    متوسط السوق: {ctx.average} ر.س")
            lines.append(f"    أقل سعر: {ctx.min_price} ر.س | أعلى سعر: {ctx.max_price} ر.س")
            if ctx.has_consensus:
                lines.append(
                    f"    ⚡ إجماع سوقي: {ctx.consensus_count} منافسين عند {ctx.consensus_price} ر.س"
                )
        else:
            lines.append("    ⚠️ لا توجد أسعار منافسين")

        # حقن RAG سياقي لهذا المنتج
        similar = similar_map.get(product.name, [])
        if similar:
            lines.append(build_similar_section(similar))

        lines.append("")

    lines.append(
        f"أعِد JSON array بـ{len(products)} عنصر بالترتيب [0..{len(products) - 1}]. "
        "لا تكتب أي نص خارج الـ JSON."
    )
    return "\n".join(lines)


def parse_batch_response(
    response: str,
    count: int,
) -> list[dict[str, Any]]:
    """يحلّل رد AI إلى قائمة قرارات منظمة.

    آمن: فشل التحليل → قرارات ``review`` افتراضية لكل منتج.
    يتوقع JSON array حيث كل عنصر يحوي:
    ``{"i": 0, "decision": "...", "suggested_price": ..., "reason": "...", "confidence": ...}``
    """
    defaults = [
        {
            "i": idx,
            "decision": "review",
            "suggested_price": 0,
            "reason": "تعذّر تحليل رد AI",
            "confidence": 0.0,
        }
        for idx in range(count)
    ]

    parsed = parse_json(response)
    if not isinstance(parsed, list):
        return defaults

    results = list(defaults)  # نسخة
    for item in parsed:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("i", -1))
        except (ValueError, TypeError):
            continue
        if 0 <= idx < count:
            results[idx] = {
                "i": idx,
                "decision": str(item.get("decision", "review")).strip().lower(),
                "suggested_price": _safe_float(item.get("suggested_price", 0)),
                "reason": str(item.get("reason", "")).strip(),
                "confidence": min(max(_safe_float(item.get("confidence", 0)), 0.0), 1.0),
            }

    return results


def _safe_float(val: Any, default: float = 0.0) -> float:
    """تحويل آمن إلى float."""
    try:
        if val is None or str(val).strip() in ("", "nan", "None"):
            return default
        return float(val)
    except (ValueError, TypeError):
        return default


# ════════════════════════════════════════════════════════════════════
#  الخدمة الرئيسية (CopilotService)
# ════════════════════════════════════════════════════════════════════

class CopilotService:
    """العقل المدبر: يربط AI + الذاكرة + الحراسات في مسار واحد نظيف.

    Parameters
    ----------
    ai_service : AIService
        خدمة AI المحقونة (سلسلة التجاوز).
    decision_repo : AIDecisionRepository
        مستودع قرارات AI (ذاكرة التعلّم).
    batch_size : int
        عدد المنتجات في كل طلب AI (افتراضي 15).
    max_items : int
        الحد الأقصى لعدد المنتجات في تحليل واحد.
    max_drop_pct : float
        القاعدة 2: الحد الأقصى لنسبة الهبوط (افتراضي 15%).
    outlier_pct : float
        القاعدة 1: عتبة الشذوذ (افتراضي 30%).
    rag_limit : int
        عدد أمثلة التعلّم العامة.
    similar_limit : int
        عدد القرارات المشابهة لكل منتج.
    """

    def __init__(
        self,
        ai_service: AIService,
        decision_repo: AIDecisionRepository,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_items: int = DEFAULT_MAX_ITEMS,
        max_drop_pct: float = DEFAULT_MAX_DROP_PCT,
        outlier_pct: float = DEFAULT_OUTLIER_PCT,
        rag_limit: int = 20,
        similar_limit: int = 5,
        feedback: Optional[FeedbackCollector] = None,
    ) -> None:
        self._ai = ai_service
        self._repo = decision_repo
        self._batch_size = batch_size
        self._max_items = max_items
        self._max_drop_pct = max_drop_pct
        self._outlier_pct = outlier_pct
        self._rag_limit = rag_limit
        self._similar_limit = similar_limit
        self._feedback = feedback

    @property
    def batch_size(self) -> int:
        return self._batch_size

    def analyze(
        self,
        products: list[ProductInput],
        *,
        progress_cb: Optional[Callable[[int, int], Any]] = None,
    ) -> list[CopilotSuggestion]:
        """يحلّل قائمة منتجات ويُعيد اقتراحات AI بعد تطبيق الحراسات.

        التدفق:
        1. تقسيم المنتجات إلى دفعات ≤batch_size
        2. جلب أمثلة RAG (مرة واحدة)
        3. لكل دفعة: بناء prompt → نداء AI → تحليل → حراسات → تسجيل
        4. إرجاع كل الاقتراحات

        Parameters
        ----------
        products : list[ProductInput]
            المنتجات المطلوب تحليلها.
        progress_cb : Optional[Callable]
            دالة رد نداء للتقدم (current, total).

        Returns
        -------
        list[CopilotSuggestion]
            اقتراحات AI مع نتائج الأمان ومعرفات قاعدة البيانات.
        """
        # ── تهيئة ──
        self._repo.ensure_table()
        capped = products[: self._max_items]
        total = len(capped)

        # جلب أمثلة التعلّم (RAG) مرة واحدة
        rag_examples = self._repo.get_learning_examples(limit=self._rag_limit)

        # معرّف الدفعة الكلي
        master_batch_id = AIDecisionRepository.new_batch_id()

        all_suggestions: list[CopilotSuggestion] = []
        processed = 0

        for start in range(0, total, self._batch_size):
            chunk = capped[start: start + self._batch_size]

            # جلب قرارات مشابهة لكل منتج (RAG سياقي)
            similar_map: dict[str, list[dict[str, Any]]] = {}
            for p in chunk:
                similar_map[p.name] = self._repo.get_similar_decisions(
                    p.name, limit=self._similar_limit,
                )

            # بناء الـ prompt
            prompt = build_batch_prompt(chunk, rag_examples, similar_map)

            # نداء AI (مع حماية من الاستثناءات)
            try:
                result: AIResult = self._ai.call(prompt, _SYSTEM_PROMPT, use_cache=False)
                response_text = result.response if result.success else ""
                ai_source = result.source if result.success else "failed"
            except Exception as exc:
                logger.warning("فشل نداء AI: %s", exc)
                response_text = ""
                ai_source = "failed"

            # تحليل الرد
            parsed = parse_batch_response(response_text, len(chunk))

            # تطبيق الحراسات + تسجيل
            for idx, (product, ai_resp) in enumerate(zip(chunk, parsed)):
                # حساب السياق السوقي
                market = compute_market_context(product.competitor_prices)

                proposed = _safe_float(ai_resp.get("suggested_price", 0))
                # إذا لم يقترح AI سعراً → نستخدم سعرنا
                if proposed <= 0:
                    proposed = product.our_price

                # تطبيق القواعد الذهبية البرمجية
                safety = validate_price_safety(
                    our_current_price=product.our_price,
                    proposed_price=proposed,
                    market_ctx=market,
                    max_drop_pct=self._max_drop_pct,
                    outlier_threshold=self._outlier_pct,
                )

                # أقرب اسم منافس
                cheapest_name = ""
                if product.competitor_names:
                    cheapest_name = product.competitor_names[0]
                cheapest_price = market.min_price if market.competitor_count > 0 else 0.0

                # بناء القرار
                ai_decision_text = ai_resp.get("decision", "review")
                # إذا الحارس أوقفه → نغيّر القرار لـ review
                if not safety.is_safe and ai_decision_text not in ("review",):
                    ai_decision_text = "review"

                reason_parts = []
                ai_reason = ai_resp.get("reason", "")
                if ai_reason:
                    reason_parts.append(ai_reason)
                if safety.warning:
                    reason_parts.append(f"⚠️ {safety.warning}")

                decision = AIDecision(
                    product_name=product.name,
                    product_id=product.product_id,
                    competitor_name=cheapest_name,
                    competitor_price=cheapest_price,
                    our_price=product.our_price,
                    market_average=market.average,
                    proposed_price=round(safety.safe_price, 2),
                    ai_reason=" | ".join(reason_parts),
                    ai_confidence=ai_resp.get("confidence", 0.0),
                    ai_decision=ai_decision_text,
                    ai_model=ai_source,
                    safety_flag=safety.flag,
                    section=product.section,
                    batch_id=master_batch_id,
                )

                # تسجيل في قاعدة البيانات
                db_id = self._repo.log_decision(decision)

                all_suggestions.append(CopilotSuggestion(
                    decision=decision,
                    safety=safety,
                    market=market,
                    db_id=db_id,
                ))

            processed += len(chunk)
            if progress_cb:
                progress_cb(processed, total)

        return all_suggestions

    # ── تسجيل تقييم المستخدم في FeedbackCollector ────────────────

    def _record_feedback(
        self, db_id: int, action: str, user_price: float,
    ) -> None:
        """يسجّل تقييم المستخدم في FeedbackCollector (اختياري — لا يكسر التدفق).

        Parameters
        ----------
        db_id : int
            معرّف القرار في قاعدة البيانات.
        action : str
            إجراء المستخدم (approved / rejected / modified).
        user_price : float
            السعر النهائي الذي اختاره المستخدم.
        """
        if self._feedback is None:
            return
        try:
            row = self._repo.get_by_id(db_id)
            if row is None:
                logger.warning("تعذّر جلب القرار %d لتسجيل التقييم", db_id)
                return
            self._feedback.record(
                decision_id=db_id,
                product_name=row.get("product_name", ""),
                section=row.get("section", ""),
                ai_price=row.get("proposed_price", 0.0),
                ai_confidence=row.get("ai_confidence", 0.0),
                action=action,
                user_price=user_price,
                brand=row.get("brand"),
            )
        except Exception:
            logger.warning(
                "فشل تسجيل التقييم للقرار %d — التدفق الأساسي لم يتأثر",
                db_id,
                exc_info=True,
            )

    # ── قرارات المستخدم ──────────────────────────────────────────

    def approve(self, db_id: int, *, price: float = 0.0) -> None:
        """يسجّل موافقة المستخدم على اقتراح AI."""
        self._repo.resolve(db_id, "approved", user_price=price)
        self._record_feedback(db_id, "approved", price)

    def reject(self, db_id: int, *, note: str = "") -> None:
        """يسجّل رفض المستخدم لاقتراح AI."""
        self._repo.resolve(db_id, "rejected", note=note)
        self._record_feedback(db_id, "rejected", 0.0)

    def modify(self, db_id: int, new_price: float, *, note: str = "") -> None:
        """يسجّل تعديل المستخدم للسعر المقترح."""
        self._repo.resolve(db_id, "modified", user_price=new_price, note=note)
        self._record_feedback(db_id, "modified", new_price)

    def get_pending(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """يجلب الاقتراحات المعلّقة."""
        return self._repo.get_pending(limit=limit)

    def get_stats(self) -> dict[str, int]:
        """إحصاءات القرارات."""
        return self._repo.get_stats()

    def split_batches(self, products: list[ProductInput]) -> list[list[ProductInput]]:
        """يقسم المنتجات إلى دفعات (مفيد للاختبار والعرض)."""
        capped = products[: self._max_items]
        batches: list[list[ProductInput]] = []
        for start in range(0, len(capped), self._batch_size):
            batches.append(capped[start: start + self._batch_size])
        return batches
