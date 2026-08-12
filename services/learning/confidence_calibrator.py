"""services/learning/confidence_calibrator.py — معاير ثقة الذكاء الاصطناعي.

يُعدِّل درجات ثقة الذكاء الاصطناعي بناءً على دقة التسعير الفعلية التاريخية:
- إذا ادّعى الذكاء الاصطناعي ثقة 90٪ لكن المستخدم رفض 40٪ من اقتراحاته،
  تصبح الثقة الحقيقية 90٪ × 60٪ = 54٪.
- يدعم المعايرة حسب السياق: علامة تجارية + قسم → علامة → قسم → شريحة → عام.
- بوابة وضع الطيار الآلي (AutoPilot): هل الثقة المعايرة تكفي للموافقة التلقائية؟

⚠️ لا يستورد streamlit — خدمة خالصة قابلة للحقن.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from services.learning.feedback_collector import (
    AccuracyStats,
    BrandFeedbackStats,
    FeedbackCollector,
)


# ---------------------------------------------------------------------------
# نموذج البيانات
# ---------------------------------------------------------------------------

@dataclass
class CalibrationResult:
    """نتيجة معايرة ثقة الذكاء الاصطناعي لقرار تسعير واحد.

    Attributes:
        raw_confidence: درجة الثقة الأصلية من الذكاء الاصطناعي (0-1).
        calibrated_confidence: الثقة المُعدَّلة بعد المعايرة (0-1).
        historical_accuracy: معدل الموافقة الفعلي التاريخي (0-1).
        adjustment_factor: مُعامل الضرب المُطبَّق على الثقة الخام.
        sample_size: عدد القرارات المُستخدمة في المعايرة.
        context_key: السياق المُستخدم (brand+section / brand / section / tier / global / none).
    """

    raw_confidence: float
    calibrated_confidence: float
    historical_accuracy: float
    adjustment_factor: float
    sample_size: int
    context_key: str


# ---------------------------------------------------------------------------
# الحد الأدنى للعينات
# ---------------------------------------------------------------------------

_MIN_SAMPLES = 3
"""الحد الأدنى لعدد القرارات لاعتماد سياق معايرة معيّن."""

_PREFERRED_SAMPLES = 5
"""عدد العينات المُفضّل لاعتماد السياقات الخاصة (brand/section/tier)."""


# ---------------------------------------------------------------------------
# الخدمة الرئيسية
# ---------------------------------------------------------------------------

class ConfidenceCalibrator:
    """معاير ثقة الذكاء الاصطناعي بناءً على الدقة التاريخية.

    يستخدم بيانات FeedbackCollector لحساب معدل الموافقة الفعلي
    ويضرب ثقة الذكاء الاصطناعي الخام في هذا المعدل للحصول على ثقة معايرة.

    الأولوية (الأكثر تحديداً أولاً):
        1. علامة تجارية + قسم (إذا توفر كلاهما وحجم العينة ≥ 5)
        2. علامة تجارية فقط (إذا توفرت وحجم العينة ≥ 5)
        3. قسم فقط (إذا توفر وحجم العينة ≥ 5)
        4. شريحة سعرية فقط (إذا توفرت وحجم العينة ≥ 5)
        5. احتياطي عام (كل القرارات)
    """

    def __init__(self, feedback: FeedbackCollector) -> None:
        """يهيّئ المعاير مع جامع التقييمات.

        Args:
            feedback: خدمة جامع التقييمات (حقن تبعية).
        """
        self._feedback = feedback

    # ------------------------------------------------------------------
    # المعايرة الرئيسية
    # ------------------------------------------------------------------

    def calibrate(
        self,
        raw_confidence: float,
        *,
        brand: str = "",
        section: str = "",
        price_tier: str = "",
    ) -> CalibrationResult:
        """يعاير درجة ثقة الذكاء الاصطناعي بناءً على الدقة التاريخية.

        يجرّب السياقات بالترتيب من الأكثر تحديداً إلى الأعم،
        ويختار أول سياق يملك عدداً كافياً من العينات.

        Args:
            raw_confidence: درجة الثقة الخام (0-1).
            brand: العلامة التجارية (اختيارية).
            section: القسم (اختيارية).
            price_tier: الشريحة السعرية (اختيارية).

        Returns:
            نتيجة المعايرة مع التفاصيل.
        """
        # 1) brand + section
        if brand and section:
            accuracy, size = self._get_brand_section_accuracy(brand, section)
            if size >= _PREFERRED_SAMPLES:
                return self._build_result(
                    raw_confidence, accuracy, size, f"brand+section:{brand}+{section}",
                )

        # 2) brand only
        if brand:
            stats = self._feedback.get_brand_stats(brand)
            accuracy, size = self._compute_accuracy(stats)
            if size >= _PREFERRED_SAMPLES:
                return self._build_result(
                    raw_confidence, accuracy, size, f"brand:{brand}",
                )

        # 3) section only
        if section:
            accuracy, size = self._get_section_accuracy(section)
            if size >= _PREFERRED_SAMPLES:
                return self._build_result(
                    raw_confidence, accuracy, size, f"section:{section}",
                )

        # 4) price_tier only
        if price_tier:
            accuracy, size = self._get_tier_accuracy(price_tier)
            if size >= _PREFERRED_SAMPLES:
                return self._build_result(
                    raw_confidence, accuracy, size, f"tier:{price_tier}",
                )

        # 5) global fallback
        global_stats = self._feedback.get_accuracy_stats(window_days=36500)
        accuracy, size = self._compute_accuracy(global_stats)
        if size >= _MIN_SAMPLES:
            return self._build_result(
                raw_confidence, accuracy, size, "global",
            )

        # لا توجد بيانات كافية — إرجاع الثقة الخام بدون تعديل
        return CalibrationResult(
            raw_confidence=raw_confidence,
            calibrated_confidence=raw_confidence,
            historical_accuracy=0.0,
            adjustment_factor=1.0,
            sample_size=0,
            context_key="none",
        )

    # ------------------------------------------------------------------
    # خريطة المعايرة
    # ------------------------------------------------------------------

    def get_calibration_map(self) -> dict[str, float]:
        """يُرجع خريطة عوامل المعايرة لكل السياقات التي تملك بيانات كافية.

        Returns:
            قاموس {سياق: عامل_المعايرة}.
            مثال: {"brand:Dior": 0.72, "section:price_raise": 0.85, "global": 0.78}
        """
        cal_map: dict[str, float] = {}

        # العلامات التجارية
        brand_stats_list = self._feedback.get_all_brand_stats(min_decisions=_MIN_SAMPLES)
        for bs in brand_stats_list:
            accuracy, _ = self._compute_accuracy(bs)
            cal_map[f"brand:{bs.brand}"] = round(accuracy, 4)

        # الأقسام — استخراج الأقسام المتميزة من قاعدة البيانات
        section_records = self._feedback._db.query(
            "SELECT DISTINCT section FROM feedback_records",
        )
        for row in section_records:
            sec = row["section"]
            accuracy, size = self._get_section_accuracy(sec)
            if size >= _MIN_SAMPLES:
                cal_map[f"section:{sec}"] = round(accuracy, 4)

        # الشرائح السعرية
        tier_rates = self._feedback.get_approval_rate_by_tier()
        for tier_name, pct_rate in tier_rates.items():
            # pct_rate هو نسبة مئوية (0-100) — تحويل إلى 0-1
            tier_records = self._feedback._db.query(
                "SELECT COUNT(*) as cnt FROM feedback_records WHERE price_range_tier = ?",
                (tier_name,),
            )
            cnt = tier_records[0]["cnt"] if tier_records else 0
            if cnt >= _MIN_SAMPLES:
                cal_map[f"tier:{tier_name}"] = round(pct_rate / 100.0, 4)

        # العام
        global_stats = self._feedback.get_accuracy_stats(window_days=36500)
        g_accuracy, g_size = self._compute_accuracy(global_stats)
        if g_size >= _MIN_SAMPLES:
            cal_map["global"] = round(g_accuracy, 4)

        return cal_map

    # ------------------------------------------------------------------
    # إعادة حساب كاملة
    # ------------------------------------------------------------------

    def recalculate(self) -> dict[str, float]:
        """يُعيد حساب جميع عوامل المعايرة ويُرجع الخريطة المُحدَّثة.

        Returns:
            خريطة المعايرة الجديدة بعد إعادة الحساب.
        """
        return self.get_calibration_map()

    # ------------------------------------------------------------------
    # بوابة الموافقة التلقائية
    # ------------------------------------------------------------------

    def should_auto_approve(
        self, calibrated_confidence: float, *, threshold: float = 0.90
    ) -> bool:
        """يحدد ما إذا كانت الثقة المعايرة تكفي للموافقة التلقائية.

        هذه البوابة تُستخدم في وضع الطيار الآلي (AutoPilot):
        إذا كانت الثقة المعايرة أعلى من الحد فيُوافَق تلقائياً.

        Args:
            calibrated_confidence: الثقة المعايرة (0-1).
            threshold: حد الموافقة التلقائية (افتراضي 0.90).

        Returns:
            True إذا تجاوزت الثقة المعايرة الحد.
        """
        return calibrated_confidence >= threshold

    # ------------------------------------------------------------------
    # مساعدات داخلية
    # ------------------------------------------------------------------

    def _compute_accuracy(
        self, stats: AccuracyStats | BrandFeedbackStats
    ) -> tuple[float, int]:
        """يحسب معدل الدقة (0-1) وحجم العينة من إحصائيات التقييم.

        Args:
            stats: إحصائيات الدقة (عامة أو لعلامة تجارية).

        Returns:
            (معدل_الموافقة_0_إلى_1, حجم_العينة).
        """
        if isinstance(stats, AccuracyStats):
            total = stats.total_decisions
            if total == 0:
                return 0.0, 0
            # approval_rate محفوظ كنسبة مئوية (0-100)
            return stats.approval_rate / 100.0, total

        if isinstance(stats, BrandFeedbackStats):
            total = stats.total
            if total == 0:
                return 0.0, 0
            # approval_rate محفوظ كنسبة مئوية (0-100)
            return stats.approval_rate / 100.0, total

        return 0.0, 0

    def _get_section_accuracy(self, section: str) -> tuple[float, int]:
        """يحسب معدل الدقة لقسم محدد من قاعدة البيانات.

        Args:
            section: اسم القسم.

        Returns:
            (معدل_الموافقة_0_إلى_1, حجم_العينة).
        """
        rows = self._feedback._db.query(
            "SELECT user_action FROM feedback_records WHERE section = ?",
            (section,),
        )
        total = len(rows)
        if total == 0:
            return 0.0, 0
        approved = sum(1 for r in rows if r["user_action"] == "approved")
        return approved / total, total

    def _get_tier_accuracy(self, tier: str) -> tuple[float, int]:
        """يحسب معدل الدقة لشريحة سعرية محددة.

        Args:
            tier: اسم الشريحة السعرية.

        Returns:
            (معدل_الموافقة_0_إلى_1, حجم_العينة).
        """
        rows = self._feedback._db.query(
            "SELECT user_action FROM feedback_records WHERE price_range_tier = ?",
            (tier,),
        )
        total = len(rows)
        if total == 0:
            return 0.0, 0
        approved = sum(1 for r in rows if r["user_action"] == "approved")
        return approved / total, total

    def _get_brand_section_accuracy(
        self, brand: str, section: str
    ) -> tuple[float, int]:
        """يحسب معدل الدقة لمزيج علامة تجارية + قسم.

        Args:
            brand: العلامة التجارية.
            section: القسم.

        Returns:
            (معدل_الموافقة_0_إلى_1, حجم_العينة).
        """
        rows = self._feedback._db.query(
            "SELECT user_action FROM feedback_records "
            "WHERE brand = ? AND section = ?",
            (brand, section),
        )
        total = len(rows)
        if total == 0:
            return 0.0, 0
        approved = sum(1 for r in rows if r["user_action"] == "approved")
        return approved / total, total

    @staticmethod
    def _build_result(
        raw_confidence: float,
        accuracy: float,
        sample_size: int,
        context_key: str,
    ) -> CalibrationResult:
        """يبني نتيجة معايرة من المُدخلات المحسوبة.

        Args:
            raw_confidence: الثقة الخام.
            accuracy: معدل الدقة (0-1).
            sample_size: حجم العينة.
            context_key: مفتاح السياق.

        Returns:
            نتيجة المعايرة.
        """
        return CalibrationResult(
            raw_confidence=raw_confidence,
            calibrated_confidence=raw_confidence * accuracy,
            historical_accuracy=accuracy,
            adjustment_factor=accuracy,
            sample_size=sample_size,
            context_key=context_key,
        )
