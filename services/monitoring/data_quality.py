"""services/monitoring/data_quality.py — خدمة تقييم جودة البيانات المُحمَّلة.

تُقيِّم جودة بيانات المنتجات قبل التحليل وتكشف المشاكل المحتملة:
- حقول مفقودة أو فارغة
- أسعار غير منطقية (صفر، سلبية، مرتفعة جداً)
- تنسيق غير متسق (خلط عربي/إنجليزي)
- بيانات قديمة (حسب أعمدة التاريخ إن وُجدت)
- أسعار شاذة إحصائياً (z-score)

⚠️ لا يستورد Streamlit أبداً. لا يعتمد على قاعدة بيانات (حسابات صِرفة).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd


# ════════════════════════════════════════════════════════════════════
#  هياكل البيانات
# ════════════════════════════════════════════════════════════════════

@dataclass
class QualityIssue:
    """مشكلة جودة واحدة مكتشَفة في البيانات.

    كل مشكلة تحمل معرّفاً فريداً وشدّة وفئة ورسالة بالعربية
    وعدد الصفوف المتأثرة وأمثلة من القيم.
    """

    issue_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    severity: str = "warning"          # error | warning | info
    category: str = "missing_field"    # missing_field | invalid_price | inconsistent_format | stale_data | outlier_price
    message: str = ""                  # وصف بالعربية
    affected_rows: int = 0
    sample_values: list[str] = field(default_factory=list)


@dataclass
class QualityScore:
    """نتيجة تقييم جودة البيانات الشاملة.

    تحتوي على درجات فرعية (0–100) لكل بُعد من أبعاد الجودة
    بالإضافة إلى قائمة المشاكل المكتشَفة.

    الدرجة الإجمالية:
        overall = completeness*0.35 + consistency*0.20 + price_validity*0.35 + freshness*0.10
    """

    overall: float = 0.0              # 0–100
    completeness: float = 0.0         # 0–100 نسبة الحقول المطلوبة غير الفارغة
    consistency: float = 0.0          # 0–100 تناسق التنسيق
    price_validity: float = 0.0       # 0–100 منطقية الأسعار
    freshness: float = 0.0            # 0–100 حداثة البيانات (100 = اليوم، 0 = >30 يوم)
    issues: list[QualityIssue] = field(default_factory=list)
    total_rows: int = 0

    @property
    def grade(self) -> str:
        """تصنيف حرفي بناءً على الدرجة الإجمالية.

        A (≥90) | B (≥75) | C (≥60) | D (≥40) | F (<40)
        """
        if self.overall >= 90:
            return "A"
        if self.overall >= 75:
            return "B"
        if self.overall >= 60:
            return "C"
        if self.overall >= 40:
            return "D"
        return "F"


# ════════════════════════════════════════════════════════════════════
#  الثوابت
# ════════════════════════════════════════════════════════════════════

_REQUIRED_COLUMNS: list[str] = ["المنتج", "السعر", "الماركة"]
_OPTIONAL_COLUMNS: list[str] = ["معرف_المنتج", "الحجم"]
_DATE_COLUMN_CANDIDATES: list[str] = ["التاريخ", "تاريخ_التحديث", "date", "updated_at"]

# حدود الأسعار (ر.س.)
_PRICE_ZERO = 0.0
_PRICE_NEGATIVE_LIMIT = 0.0
_PRICE_HIGH_THRESHOLD = 10_000.0
_PRICE_LOW_THRESHOLD = 5.0


# ════════════════════════════════════════════════════════════════════
#  خدمة تقييم الجودة
# ════════════════════════════════════════════════════════════════════

class DataQualityScorer:
    """مُقيِّم جودة البيانات — بدون حالة، حسابات صِرفة.

    يفحص DataFrame ويعيد ``QualityScore`` شاملة تتضمن درجات فرعية
    وقائمة مشاكل ``QualityIssue`` مفصَّلة بالعربية.

    الاستخدام::

        scorer = DataQualityScorer()
        result = scorer.score(df)
        print(result.grade, result.overall)
        for issue in result.issues:
            print(issue.severity, issue.message)
    """

    def __init__(self) -> None:
        """تهيئة المُقيِّم — بلا اعتماديات (حسابات صِرفة بلا حالة)."""
        pass

    # ── النقطة الرئيسية ─────────────────────────────────────────

    def score(self, df: pd.DataFrame) -> QualityScore:
        """يُقيِّم جودة DataFrame ويعيد ``QualityScore`` شاملة.

        يحسب أبعاد الجودة الأربعة ويجمعها بأوزان ثابتة:
        - الاكتمال (35%)
        - صحة الأسعار (35%)
        - التناسق (20%)
        - الحداثة (10%)

        المعاملات:
            df: جدول المنتجات (أعمدة عربية).

        المخرجات:
            QualityScore مع كل الدرجات والمشاكل.
        """
        total_rows = len(df)

        if total_rows == 0:
            return QualityScore(
                overall=0.0,
                completeness=0.0,
                consistency=0.0,
                price_validity=0.0,
                freshness=0.0,
                issues=[
                    QualityIssue(
                        severity="error",
                        category="missing_field",
                        message="البيانات فارغة — لا توجد صفوف للتحليل",
                        affected_rows=0,
                    )
                ],
                total_rows=0,
            )

        all_issues: list[QualityIssue] = []

        completeness, comp_issues = self.check_completeness(df)
        all_issues.extend(comp_issues)

        price_validity, price_issues = self.check_price_validity(df)
        all_issues.extend(price_issues)

        consistency, cons_issues = self.check_consistency(df)
        all_issues.extend(cons_issues)

        freshness, fresh_issues = self.check_freshness(df)
        all_issues.extend(fresh_issues)

        outlier_issues = self.detect_price_outliers(df)
        all_issues.extend(outlier_issues)

        overall = (
            completeness * 0.35
            + consistency * 0.20
            + price_validity * 0.35
            + freshness * 0.10
        )
        overall = max(0.0, min(100.0, overall))

        return QualityScore(
            overall=overall,
            completeness=completeness,
            consistency=consistency,
            price_validity=price_validity,
            freshness=freshness,
            issues=all_issues,
            total_rows=total_rows,
        )

    # ── فحص الاكتمال ────────────────────────────────────────────

    def check_completeness(self, df: pd.DataFrame) -> tuple[float, list[QualityIssue]]:
        """يفحص وجود الأعمدة المطلوبة وامتلاء الخلايا.

        يتحقق من:
        1. وجود كل عمود مطلوب (المنتج، السعر، الماركة).
        2. نسبة الخلايا غير الفارغة في الأعمدة الموجودة.

        المعاملات:
            df: جدول المنتجات.

        المخرجات:
            (درجة 0–100, قائمة مشاكل).
        """
        issues: list[QualityIssue] = []
        total_rows = len(df)

        if total_rows == 0:
            return 0.0, [
                QualityIssue(
                    severity="error",
                    category="missing_field",
                    message="البيانات فارغة",
                    affected_rows=0,
                )
            ]

        # فحص وجود الأعمدة المطلوبة
        missing_cols = [c for c in _REQUIRED_COLUMNS if c not in df.columns]
        if missing_cols:
            issues.append(QualityIssue(
                severity="error",
                category="missing_field",
                message=f"أعمدة مطلوبة مفقودة: {', '.join(missing_cols)}",
                affected_rows=total_rows,
                sample_values=missing_cols[:3],
            ))

        # حساب نسبة الامتلاء للأعمدة الموجودة
        present_required = [c for c in _REQUIRED_COLUMNS if c in df.columns]
        if not present_required:
            return 0.0, issues

        filled_cells = 0
        total_cells = 0
        for col in present_required:
            total_cells += total_rows
            # عدّ الخلايا غير الفارغة وغير NA
            non_empty = df[col].dropna()
            # تجاهل السلاسل الفارغة أيضاً
            if pd.api.types.is_string_dtype(non_empty):
                non_empty = non_empty[non_empty.astype(str).str.strip() != ""]
            filled_cells += len(non_empty)
            empty_count = total_rows - len(non_empty)
            if empty_count > 0:
                issues.append(QualityIssue(
                    severity="warning",
                    category="missing_field",
                    message=f"العمود '{col}' يحتوي على {empty_count} قيمة فارغة",
                    affected_rows=empty_count,
                ))

        completeness = (filled_cells / total_cells * 100.0) if total_cells > 0 else 0.0

        # خصم إضافي لكل عمود مطلوب مفقود
        missing_penalty = len(missing_cols) / len(_REQUIRED_COLUMNS) * 100.0
        completeness = max(0.0, completeness - missing_penalty)

        return completeness, issues

    # ── فحص صحة الأسعار ─────────────────────────────────────────

    def check_price_validity(self, df: pd.DataFrame) -> tuple[float, list[QualityIssue]]:
        """يفحص منطقية الأسعار (صفر، سلبية، مرتفعة جداً، منخفضة جداً).

        قواعد الفحص:
        - سعر = 0 → خطأ
        - سعر < 0 → خطأ
        - سعر > 10,000 ر.س. → تحذير (قد يكون خطأ إدخال)
        - سعر < 5 ر.س. → تحذير (منخفض بشكل مريب)

        المعاملات:
            df: جدول المنتجات.

        المخرجات:
            (درجة 0–100, قائمة مشاكل).
        """
        issues: list[QualityIssue] = []

        if "السعر" not in df.columns:
            return 0.0, [
                QualityIssue(
                    severity="error",
                    category="missing_field",
                    message="عمود 'السعر' مفقود — لا يمكن فحص صحة الأسعار",
                    affected_rows=len(df),
                )
            ]

        total_rows = len(df)
        if total_rows == 0:
            return 100.0, []

        # تحويل لأرقام
        prices = pd.to_numeric(df["السعر"], errors="coerce")
        nan_count = int(prices.isna().sum())
        valid_prices = prices.dropna()

        error_count = 0
        warning_count = 0

        # أسعار غير رقمية
        if nan_count > 0:
            non_numeric_vals = df["السعر"][prices.isna()]
            issues.append(QualityIssue(
                severity="error",
                category="invalid_price",
                message=f"{nan_count} قيمة سعر غير رقمية",
                affected_rows=nan_count,
                sample_values=[str(v) for v in non_numeric_vals.head(3).tolist()],
            ))
            error_count += nan_count

        if len(valid_prices) > 0:
            # أسعار صفرية
            zero_mask = valid_prices == 0
            zero_count = int(zero_mask.sum())
            if zero_count > 0:
                issues.append(QualityIssue(
                    severity="error",
                    category="invalid_price",
                    message=f"{zero_count} منتج بسعر صفر",
                    affected_rows=zero_count,
                    sample_values=["0"] * min(3, zero_count),
                ))
                error_count += zero_count

            # أسعار سلبية
            neg_mask = valid_prices < 0
            neg_count = int(neg_mask.sum())
            if neg_count > 0:
                neg_vals = valid_prices[neg_mask]
                issues.append(QualityIssue(
                    severity="error",
                    category="invalid_price",
                    message=f"{neg_count} منتج بسعر سلبي",
                    affected_rows=neg_count,
                    sample_values=[str(v) for v in neg_vals.head(3).tolist()],
                ))
                error_count += neg_count

            # أسعار مرتفعة جداً
            high_mask = valid_prices > _PRICE_HIGH_THRESHOLD
            high_count = int(high_mask.sum())
            if high_count > 0:
                high_vals = valid_prices[high_mask]
                issues.append(QualityIssue(
                    severity="warning",
                    category="invalid_price",
                    message=f"{high_count} منتج بسعر أعلى من {_PRICE_HIGH_THRESHOLD:,.0f} ر.س. — قد يكون خطأ إدخال",
                    affected_rows=high_count,
                    sample_values=[str(v) for v in high_vals.head(3).tolist()],
                ))
                warning_count += high_count

            # أسعار منخفضة جداً (> 0 و < 5)
            low_mask = (valid_prices > 0) & (valid_prices < _PRICE_LOW_THRESHOLD)
            low_count = int(low_mask.sum())
            if low_count > 0:
                low_vals = valid_prices[low_mask]
                issues.append(QualityIssue(
                    severity="warning",
                    category="invalid_price",
                    message=f"{low_count} منتج بسعر أقل من {_PRICE_LOW_THRESHOLD} ر.س. — منخفض بشكل مريب",
                    affected_rows=low_count,
                    sample_values=[str(v) for v in low_vals.head(3).tolist()],
                ))
                warning_count += low_count

        # حساب الدرجة: الأخطاء تخصم أكثر من التحذيرات
        if total_rows > 0:
            error_penalty = (error_count / total_rows) * 100.0
            warning_penalty = (warning_count / total_rows) * 50.0
            validity = max(0.0, 100.0 - error_penalty - warning_penalty)
        else:
            validity = 100.0

        return validity, issues

    # ── فحص التناسق ─────────────────────────────────────────────

    def check_consistency(self, df: pd.DataFrame) -> tuple[float, list[QualityIssue]]:
        """يفحص تناسق تنسيق البيانات.

        يتحقق من:
        - خلط لغات (عربي/إنجليزي) في نفس العمود النصي
        - وحدات مفقودة في عمود الحجم (إن وُجد)

        المعاملات:
            df: جدول المنتجات.

        المخرجات:
            (درجة 0–100, قائمة مشاكل).
        """
        issues: list[QualityIssue] = []
        total_rows = len(df)

        if total_rows == 0:
            return 100.0, []

        penalty = 0.0

        # فحص خلط اللغات في عمود المنتج
        if "المنتج" in df.columns:
            names = df["المنتج"].dropna().astype(str)
            if len(names) > 0:
                has_arabic = names.str.contains("[\u0600-\u06FF]", regex=True, na=False)
                has_latin = names.str.contains(r"[a-zA-Z]", regex=True, na=False)
                mixed_mask = has_arabic & has_latin
                mixed_count = int(mixed_mask.sum())

                # نحسب نسبة الخلط فقط إذا كانت ملحوظة
                arabic_only = int((has_arabic & ~has_latin).sum())
                latin_only = int((has_latin & ~has_arabic).sum())

                if arabic_only > 0 and latin_only > 0:
                    # هناك أسماء بالعربي فقط وأخرى بالإنجليزي فقط
                    minority = min(arabic_only, latin_only)
                    inconsistency_ratio = minority / len(names)
                    if inconsistency_ratio > 0.05:
                        issues.append(QualityIssue(
                            severity="warning",
                            category="inconsistent_format",
                            message=f"خلط لغات في أسماء المنتجات: {arabic_only} بالعربية و{latin_only} بالإنجليزية",
                            affected_rows=arabic_only + latin_only,
                        ))
                        penalty += inconsistency_ratio * 50.0

        # فحص عمود الحجم (إن وُجد)
        if "الحجم" in df.columns:
            sizes = df["الحجم"].dropna().astype(str).str.strip()
            non_empty_sizes = sizes[sizes != ""]
            if len(non_empty_sizes) > 0:
                # فحص وجود وحدة قياس
                has_unit = non_empty_sizes.str.contains(
                    r"(مل|لتر|جم|كجم|ml|l|g|kg|oz|fl)", regex=True, case=False, na=False
                )
                no_unit_count = int((~has_unit).sum())
                if no_unit_count > 0:
                    no_unit_vals = non_empty_sizes[~has_unit]
                    issues.append(QualityIssue(
                        severity="info",
                        category="inconsistent_format",
                        message=f"{no_unit_count} قيمة حجم بدون وحدة قياس",
                        affected_rows=no_unit_count,
                        sample_values=[str(v) for v in no_unit_vals.head(3).tolist()],
                    ))
                    penalty += (no_unit_count / total_rows) * 20.0

        consistency = max(0.0, 100.0 - penalty)
        return consistency, issues

    # ── فحص الحداثة ─────────────────────────────────────────────

    def check_freshness(self, df: pd.DataFrame) -> tuple[float, list[QualityIssue]]:
        """يفحص حداثة البيانات بناءً على أعمدة التاريخ (إن وُجدت).

        إذا لم تُوجد أعمدة تاريخ، يُفترض أن البيانات حديثة (100).

        حساب الحداثة:
        - اليوم = 100
        - كل يوم تأخير يخصم ~3.33 نقطة
        - أكثر من 30 يوم = 0

        المعاملات:
            df: جدول المنتجات.

        المخرجات:
            (درجة 0–100, قائمة مشاكل).
        """
        issues: list[QualityIssue] = []

        # البحث عن عمود تاريخ
        date_col: Optional[str] = None
        for candidate in _DATE_COLUMN_CANDIDATES:
            if candidate in df.columns:
                date_col = candidate
                break

        if date_col is None:
            # لا يوجد عمود تاريخ → نفترض الحداثة
            return 100.0, []

        # محاولة تحويل التواريخ
        try:
            dates = pd.to_datetime(df[date_col], errors="coerce")
        except Exception:
            return 100.0, []

        valid_dates = dates.dropna()
        if len(valid_dates) == 0:
            return 100.0, []

        now = pd.Timestamp.now()
        max_date = valid_dates.max()
        days_old = (now - max_date).days

        if days_old < 0:
            days_old = 0

        # حساب الدرجة: 100 لليوم، 0 لأكثر من 30 يوم
        freshness = max(0.0, 100.0 - (days_old * 100.0 / 30.0))

        if days_old > 30:
            issues.append(QualityIssue(
                severity="warning",
                category="stale_data",
                message=f"البيانات قديمة: آخر تحديث منذ {days_old} يوم",
                affected_rows=len(df),
            ))
        elif days_old > 7:
            issues.append(QualityIssue(
                severity="info",
                category="stale_data",
                message=f"البيانات ليست حديثة جداً: آخر تحديث منذ {days_old} يوم",
                affected_rows=len(df),
            ))

        return freshness, issues

    # ── كشف الأسعار الشاذة ──────────────────────────────────────

    def detect_price_outliers(
        self, df: pd.DataFrame, *, z_threshold: float = 3.0
    ) -> list[QualityIssue]:
        """يكشف الأسعار الشاذة إحصائياً باستخدام z-score.

        يحسب z-score لكل سعر ويُبلِّغ عن القيم التي تتجاوز الحد.

        المعاملات:
            df: جدول المنتجات.
            z_threshold: حد z-score للاعتبار شذوذاً (افتراضي: 3.0).

        المخرجات:
            قائمة مشاكل من نوع outlier_price.
        """
        issues: list[QualityIssue] = []

        if "السعر" not in df.columns:
            return issues

        prices = pd.to_numeric(df["السعر"], errors="coerce").dropna()
        # نحتاج 3 قيم على الأقل لحساب z-score ذي معنى
        if len(prices) < 3:
            return issues

        # استبعاد الأصفار والسلبيات من حساب الإحصائيات
        positive_prices = prices[prices > 0]
        if len(positive_prices) < 3:
            return issues

        mean = positive_prices.mean()
        std = positive_prices.std()

        if std == 0 or pd.isna(std):
            return issues

        z_scores = ((positive_prices - mean) / std).abs()
        outlier_mask = z_scores > z_threshold
        outlier_count = int(outlier_mask.sum())

        if outlier_count > 0:
            outlier_vals = positive_prices[outlier_mask]
            issues.append(QualityIssue(
                severity="warning",
                category="outlier_price",
                message=f"{outlier_count} سعر شاذ إحصائياً (z-score > {z_threshold})",
                affected_rows=outlier_count,
                sample_values=[str(v) for v in outlier_vals.head(3).tolist()],
            ))

        return issues
