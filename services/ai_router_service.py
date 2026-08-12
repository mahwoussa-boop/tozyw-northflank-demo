"""services/ai_router_service.py — خدمة التوجيه الذكي بالذكاء الاصطناعي.

تأخذ منتجات «قيد المراجعة» أو «مستبعدة» وتطلب من AI تصنيف كل منتج:
- MATCH  → ينتقل إلى أقسام الأسعار (رفع/خفض/موافق)
- MISSING → ينتقل إلى قسم المفقودات
- UNSURE → يبقى في المراجعة/المستبعد

القواعد الحاكمة:
1. لا استيراد Streamlit مطلقاً (خدمة نقية).
2. كل خطأ → المنتج يبقى مكانه (لا فقدان صامت).
3. التحقق الحسابي: مجموع المُوزَّعين = الإجمالي الأصلي.
4. الدفعات ≤8 عناصر لتفادي تجاوز سياق النموذج.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import pandas as pd

from conf.constants import (
    COL_BRAND,
    COL_COMP_NAME,
    COL_COMP_PRICE,
    COL_DECISION,
    COL_MATCH_RATIO,
    COL_OUR_ID,
    COL_OUR_NAME,
    COL_OUR_PRICE,
)
from services.ai_service import AIService

# ── التسجيل ──────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)


def _ai_failure_reason() -> str:
    """يسحب آخر خطأ حقيقي من محرّك AI بدل رسالة عامة (لا فقدان للسبب).

    مثال: «حدّ المفتاح مُستنفَد (403)» بدل «لم تُعاد استجابة» الغامضة.
    """
    try:
        from engines.ai_engine import get_last_errors  # type: ignore

        errs = get_last_errors()
        if errs:
            return str(errs[0])[:160]
    except Exception as exc:
        logger.debug("تعذّر جلب أخطاء AI: %s", exc)
    return "لم تُعاد استجابة"

# ── استيراد البرومبتات (يُنشأ لاحقاً في services/ai_prompts.py) ──────
# الاستيراد كسول لتفادي الفشل عند عدم وجود الملف بعد
_prompts_loaded = False
_build_routing_prompt: Optional[Callable] = None
_build_salvage_prompt: Optional[Callable] = None
_ROUTING_SYSTEM_PROMPT: str = ""
_SALVAGE_SYSTEM_PROMPT: str = ""
_parse_ai_routing_response: Optional[Callable] = None


def _ensure_prompts() -> None:
    """تحمّل وحدة البرومبتات كسولاً عند أول استخدام."""
    global _prompts_loaded, _build_routing_prompt, _build_salvage_prompt
    global _ROUTING_SYSTEM_PROMPT, _SALVAGE_SYSTEM_PROMPT, _parse_ai_routing_response
    if _prompts_loaded:
        return
    from services.ai_prompts import (  # type: ignore[import-untyped]
        ROUTING_SYSTEM_PROMPT,
        SALVAGE_SYSTEM_PROMPT,
        build_routing_prompt,
        build_salvage_prompt,
        parse_ai_routing_response,
    )
    _build_routing_prompt = build_routing_prompt
    _build_salvage_prompt = build_salvage_prompt
    _ROUTING_SYSTEM_PROMPT = ROUTING_SYSTEM_PROMPT
    _SALVAGE_SYSTEM_PROMPT = SALVAGE_SYSTEM_PROMPT
    _parse_ai_routing_response = parse_ai_routing_response
    _prompts_loaded = True


# ═══════════════════════════════════════════════════════════════════════
#  هياكل البيانات
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class RouteResult:
    """نتيجة توجيه منتجات المراجعة.

    - upgraded   : منتجات تأكّدت مطابقتها → تنتقل لأقسام الأسعار.
    - to_missing : منتجات تأكّد غيابها → تنتقل لقسم المفقودات.
    - stayed_review : منتجات بقيت قيد المراجعة (ثقة منخفضة أو خطأ).
    - errors     : أخطاء حدثت أثناء المعالجة.
    """
    upgraded: pd.DataFrame = field(default_factory=pd.DataFrame)
    to_missing: pd.DataFrame = field(default_factory=pd.DataFrame)
    stayed_review: pd.DataFrame = field(default_factory=pd.DataFrame)
    errors: list[str] = field(default_factory=list)


@dataclass
class SalvageResult:
    """نتيجة إنقاذ منتجات مستبعدة.

    - salvaged_matches : مستبعدات تبيّن أنها مطابقة فعلاً.
    - salvaged_missing : مستبعدات تبيّن أنها مفقودة.
    - stayed_excluded  : مستبعدات بقيت مستبعدة.
    - total_checked    : عدد العناصر التي فُحصت فعلاً.
    - errors           : أخطاء حدثت أثناء المعالجة.
    """
    salvaged_matches: pd.DataFrame = field(default_factory=pd.DataFrame)
    salvaged_missing: pd.DataFrame = field(default_factory=pd.DataFrame)
    stayed_excluded: pd.DataFrame = field(default_factory=pd.DataFrame)
    total_checked: int = 0
    errors: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════
#  أدوات مساعدة داخلية
# ═══════════════════════════════════════════════════════════════════════

def _safe_str(value: Any) -> str:
    """تحوّل قيمة إلى نص آمن، تعالج NaN/None/فارغ.

    >>> _safe_str(None)
    ''
    >>> _safe_str(float('nan'))
    ''
    >>> _safe_str(42.5)
    '42.5'
    """
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


# ═══════════════════════════════════════════════════════════════════════
#  خدمة التوجيه الرئيسية
# ═══════════════════════════════════════════════════════════════════════

class AIRouterService:
    """خدمة توجيه المنتجات بالذكاء الاصطناعي.

    تستقبل DataFrame من المراجعة أو المستبعد، ترسل دفعات إلى AI،
    وتوزّع المنتجات حسب قرار AI ومستوى الثقة.

    المبادئ:
    - لا فقدان صامت: كل منتج يظهر في إحدى القوائم الثلاث.
    - كل خطأ → المنتج يبقى مكانه.
    - الدفعات ≤ batch_size لتفادي تجاوز السياق.
    """

    def __init__(
        self,
        ai_service: AIService,
        confidence_threshold: float = 0.85,
    ) -> None:
        """تهيئة الخدمة.

        Args:
            ai_service: خدمة AI المنسّقة (تدوير مزودات + كاش).
            confidence_threshold: عتبة الثقة الدنيا لقبول قرار AI (0-1).
        """
        self.ai = ai_service
        self.threshold = confidence_threshold

    # ─── توجيه المراجعة ──────────────────────────────────────────────

    def route_review(
        self,
        review_df: pd.DataFrame,
        batch_size: int = 8,
        max_items: int = 300,
    ) -> RouteResult:
        """توجّه منتجات المراجعة إلى أقسامها النهائية عبر AI.

        Args:
            review_df: DataFrame يحوي منتجات «قيد المراجعة».
            batch_size: حجم الدفعة المُرسلة إلى AI.
            max_items: حدّ التكلفة — أقصى عدد يُرسَل للذكاء؛ الفائض يبقى في
                المراجعة (لا فقدان). يحمي الرصيد عند ضخامة قسم المراجعة.

        Returns:
            RouteResult مع توزيع المنتجات على الأقسام الثلاثة.
        """
        errors: list[str] = []

        # ── حالة خاصة: DataFrame فارغ ─────────────────────────────
        if review_df.empty:
            return RouteResult(
                upgraded=pd.DataFrame(),
                to_missing=pd.DataFrame(),
                stayed_review=pd.DataFrame(),
                errors=errors,
            )

        # ── حالة خاصة: AI غير مهيّأ ──────────────────────────────
        if not self.ai.any_configured:
            errors.append("لا يوجد مزوّد AI مُهيّأ — بقيت جميع المنتجات في المراجعة")
            return RouteResult(
                upgraded=pd.DataFrame(),
                to_missing=pd.DataFrame(),
                stayed_review=review_df.copy(),
                errors=errors,
            )

        # ── تحميل البرومبتات ─────────────────────────────────────
        try:
            _ensure_prompts()
        except ImportError as exc:
            errors.append(f"تعذّر تحميل البرومبتات: {exc}")
            return RouteResult(
                upgraded=pd.DataFrame(),
                to_missing=pd.DataFrame(),
                stayed_review=review_df.copy(),
                errors=errors,
            )

        # ── حدّ التكلفة: يُفحص أول max_items فقط؛ الفائض يبقى في المراجعة ──
        original_total = len(review_df)
        overflow = pd.DataFrame()
        if max_items is not None and original_total > max_items:
            overflow = review_df.iloc[max_items:].copy()
            review_df = review_df.iloc[:max_items]
            errors.append(
                f"حدّ التكلفة: فُحص أول {max_items} من {original_total} — "
                f"الباقي ({len(overflow)}) بقي في المراجعة"
            )

        # ── معالجة الدفعات ────────────────────────────────────────
        upgrade_indices: list[int] = []
        missing_indices: list[int] = []
        stay_indices: list[int] = []

        total = len(review_df)
        for start in range(0, total, batch_size):
            end = min(start + batch_size, total)
            batch_df = review_df.iloc[start:end]
            batch_data = self._extract_batch_data(batch_df)

            try:
                prompt = _build_routing_prompt(batch_data)  # type: ignore[misc]
                ai_result = self.ai.call(prompt, _ROUTING_SYSTEM_PROMPT)

                if not ai_result.success or not ai_result.response:
                    # فشل AI → كل الدفعة تبقى في المراجعة
                    errors.append(
                        f"فشل AI للدفعة {start}-{end}: {_ai_failure_reason()}"
                    )
                    stay_indices.extend(range(start, end))
                    continue

                # تحليل القرارات
                decisions = _parse_ai_routing_response(ai_result.response)  # type: ignore[misc]
                up_idx, miss_idx, stay_idx = self._apply_decisions(
                    batch_df, decisions, self.threshold, offset=start,
                )
                upgrade_indices.extend(up_idx)
                missing_indices.extend(miss_idx)
                stay_indices.extend(stay_idx)

            except Exception as exc:
                # أي خطأ غير متوقع → كل الدفعة تبقى في المراجعة
                logger.warning("خطأ في دفعة التوجيه %d-%d: %s", start, end, exc)
                errors.append(f"خطأ في الدفعة {start}-{end}: {exc}")
                stay_indices.extend(range(start, end))

        # ── بناء DataFrames النتيجة ───────────────────────────────
        upgraded = review_df.iloc[upgrade_indices].copy() if upgrade_indices else pd.DataFrame()
        to_missing = review_df.iloc[missing_indices].copy() if missing_indices else pd.DataFrame()
        stayed_review = review_df.iloc[stay_indices].copy() if stay_indices else pd.DataFrame()

        # الفائض عن حدّ التكلفة يُعاد إلى المراجعة (يُحفظ قانون عدم الفقدان)
        if not overflow.empty:
            stayed_review = (
                pd.concat([stayed_review, overflow], ignore_index=True)
                if not stayed_review.empty else overflow
            )

        # ── التحقق الحسابي: لا فقدان (يقارن بالإجمالي الأصلي قبل الحدّ) ──
        total_routed = len(upgraded) + len(to_missing) + len(stayed_review)
        if total_routed != original_total:
            raise AssertionError(
                f"خطأ حسابي في التوجيه: {total_routed} ≠ {original_total} "
                f"(upgraded={len(upgraded)}, missing={len(to_missing)}, "
                f"stayed={len(stayed_review)})"
            )

        return RouteResult(
            upgraded=upgraded,
            to_missing=to_missing,
            stayed_review=stayed_review,
            errors=errors,
        )

    # ─── إنقاذ المستبعدات ────────────────────────────────────────

    def salvage_excluded(
        self,
        excluded_df: pd.DataFrame,
        max_items: int = 500,
        batch_size: int = 8,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> SalvageResult:
        """تفحص المستبعدات وتُنقذ ما يستحق الانتقال لأقسام أخرى.

        Args:
            excluded_df: DataFrame يحوي المنتجات المستبعدة.
            max_items: الحد الأقصى للعناصر المفحوصة (لتفادي التكلفة).
            batch_size: حجم الدفعة المُرسلة إلى AI.
            progress_cb: دالة ردّ نداء للتقدم ``(processed, total)``.

        Returns:
            SalvageResult مع المنتجات المُنقذة والمتبقية.
        """
        errors: list[str] = []

        # ── حالة خاصة: DataFrame فارغ ─────────────────────────────
        if excluded_df.empty:
            return SalvageResult(
                salvaged_matches=pd.DataFrame(),
                salvaged_missing=pd.DataFrame(),
                stayed_excluded=pd.DataFrame(),
                total_checked=0,
                errors=errors,
            )

        # ── حالة خاصة: AI غير مهيّأ ──────────────────────────────
        if not self.ai.any_configured:
            errors.append("لا يوجد مزوّد AI مُهيّأ — بقيت جميع المستبعدات كما هي")
            return SalvageResult(
                salvaged_matches=pd.DataFrame(),
                salvaged_missing=pd.DataFrame(),
                stayed_excluded=excluded_df.copy(),
                total_checked=0,
                errors=errors,
            )

        # ── تحميل البرومبتات ─────────────────────────────────────
        try:
            _ensure_prompts()
        except ImportError as exc:
            errors.append(f"تعذّر تحميل البرومبتات: {exc}")
            return SalvageResult(
                salvaged_matches=pd.DataFrame(),
                salvaged_missing=pd.DataFrame(),
                stayed_excluded=excluded_df.copy(),
                total_checked=0,
                errors=errors,
            )

        # ── تقسيم: فقط أول max_items ─────────────────────────────
        to_check = excluded_df.iloc[:max_items]
        not_checked = excluded_df.iloc[max_items:]
        total = len(to_check)

        # ── معالجة الدفعات ────────────────────────────────────────
        match_indices: list[int] = []
        missing_indices: list[int] = []
        stay_indices: list[int] = []

        processed = 0
        for start in range(0, total, batch_size):
            end = min(start + batch_size, total)
            batch_df = to_check.iloc[start:end]
            batch_data = self._extract_batch_data(batch_df)

            try:
                prompt = _build_salvage_prompt(batch_data)  # type: ignore[misc]
                ai_result = self.ai.call(prompt, _SALVAGE_SYSTEM_PROMPT)

                if not ai_result.success or not ai_result.response:
                    errors.append(
                        f"فشل AI لدفعة الإنقاذ {start}-{end}: {_ai_failure_reason()}"
                    )
                    stay_indices.extend(range(start, end))
                else:
                    decisions = _parse_ai_routing_response(ai_result.response)  # type: ignore[misc]
                    up_idx, miss_idx, stay_idx = self._apply_decisions(
                        batch_df, decisions, self.threshold, offset=start,
                    )
                    match_indices.extend(up_idx)
                    missing_indices.extend(miss_idx)
                    stay_indices.extend(stay_idx)

            except Exception as exc:
                logger.warning("خطأ في دفعة الإنقاذ %d-%d: %s", start, end, exc)
                errors.append(f"خطأ في دفعة الإنقاذ {start}-{end}: {exc}")
                stay_indices.extend(range(start, end))

            # ── تقرير التقدم ──────────────────────────────────────
            processed = end
            if progress_cb is not None:
                try:
                    progress_cb(processed, total)
                except Exception:
                    pass  # دالة التقدم اختيارية — لا تعطّل المعالجة

        # ── بناء DataFrames النتيجة ───────────────────────────────
        salvaged_matches = (
            to_check.iloc[match_indices].copy() if match_indices else pd.DataFrame()
        )
        salvaged_missing = (
            to_check.iloc[missing_indices].copy() if missing_indices else pd.DataFrame()
        )
        # المتبقي = ما لم يُنقذ من المفحوص + ما لم يُفحص أصلاً
        stayed_from_checked = (
            to_check.iloc[stay_indices].copy() if stay_indices else pd.DataFrame()
        )
        stayed_excluded = pd.concat(
            [stayed_from_checked, not_checked.copy()],
            ignore_index=True,
        ) if not not_checked.empty or stay_indices else stayed_from_checked

        # ── التحقق الحسابي: لا فقدان ─────────────────────────────
        total_from_checked = len(salvaged_matches) + len(salvaged_missing) + len(stayed_from_checked)
        if total_from_checked != total:
            raise AssertionError(
                f"خطأ حسابي في الإنقاذ: {total_from_checked} ≠ {total} "
                f"(matches={len(salvaged_matches)}, missing={len(salvaged_missing)}, "
                f"stayed={len(stayed_from_checked)})"
            )

        return SalvageResult(
            salvaged_matches=salvaged_matches,
            salvaged_missing=salvaged_missing,
            stayed_excluded=stayed_excluded,
            total_checked=total,
            errors=errors,
        )

    # ─── أدوات مساعدة داخلية ─────────────────────────────────────

    def _extract_batch_data(self, df_slice: pd.DataFrame) -> list[dict[str, str]]:
        """تستخرج الأعمدة المطلوبة من شريحة DataFrame إلى قائمة قواميس.

        كل قاموس يحوي المفاتيح: product_name, product_id, our_price,
        brand, decision, match_ratio, competitor_name, competitor_price.
        القيم نصّية آمنة (بدون NaN).

        Args:
            df_slice: شريحة DataFrame للاستخراج منها.

        Returns:
            قائمة قواميس بعدد صفوف الشريحة.
        """
        # خريطة الأعمدة: مفتاح القاموس → اسم العمود في DataFrame
        # المفاتيح تتوافق مع ما تتوقعه دوال build_routing_prompt / build_salvage_prompt
        column_map: dict[str, str] = {
            "our_name": COL_OUR_NAME,
            "our_price": COL_OUR_PRICE,
            "comp_name": COL_COMP_NAME,
            "comp_price": COL_COMP_PRICE,
            "match_score": COL_MATCH_RATIO,
            "brand": COL_BRAND,
            "decision": COL_DECISION,
        }
        rows: list[dict[str, str]] = []
        # ⚡ PERFORMANCE FIX: استبدال iterrows البطيء بـ to_dict('records')
        for row in df_slice.to_dict('records'):
            item: dict[str, str] = {}
            for key, col in column_map.items():
                item[key] = _safe_str(row.get(col))
            rows.append(item)
        return rows

    @staticmethod
    def _apply_decisions(
        df: pd.DataFrame,
        decisions: list[dict[str, Any]],
        threshold: float,
        *,
        offset: int = 0,
    ) -> tuple[list[int], list[int], list[int]]:
        """تطبّق قرارات AI على شريحة DataFrame وتُعيد مؤشرات التوزيع.

        Args:
            df: شريحة الدفعة الأصلية.
            decisions: قائمة قرارات AI، كل قرار قاموس بمفاتيح
                       ``match_type`` و ``confidence``.
            threshold: عتبة الثقة (0-1).
            offset: إزاحة المؤشرات (موقع بداية الدفعة في DataFrame الأصلي).

        Returns:
            ثلاثي (upgrade_indices, missing_indices, stay_indices) —
            مؤشرات نسبيّة إلى DataFrame الأصلي (= offset + i).
        """
        upgrade_indices: list[int] = []
        missing_indices: list[int] = []
        stay_indices: list[int] = []

        batch_len = len(df)

        # إذا لم تُعاد قرارات صالحة → الكل يبقى
        if not decisions or not isinstance(decisions, list):
            stay_indices.extend(range(offset, offset + batch_len))
            return upgrade_indices, missing_indices, stay_indices

        for i in range(batch_len):
            idx = offset + i

            # ── لا قرار لهذا الصف ────────────────────────────────
            if i >= len(decisions):
                stay_indices.append(idx)
                continue

            decision = decisions[i]

            # ── قرار غير صالح (ليس قاموساً) ──────────────────────
            if not isinstance(decision, dict):
                stay_indices.append(idx)
                continue

            match_type = str(decision.get("match_type", "")).upper().strip()
            try:
                confidence = float(decision.get("confidence", 0))
            except (TypeError, ValueError):
                confidence = 0.0

            # ── تطبيق عتبة الثقة ─────────────────────────────────
            if match_type == "MATCH" and confidence >= threshold:
                upgrade_indices.append(idx)
            elif match_type == "MISSING" and confidence >= threshold:
                missing_indices.append(idx)
            else:
                # ثقة منخفضة أو نوع غير معروف → يبقى
                stay_indices.append(idx)

        return upgrade_indices, missing_indices, stay_indices
