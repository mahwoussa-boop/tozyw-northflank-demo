"""tests/test_data_quality.py — اختبارات خدمة تقييم جودة البيانات.

يغطي 13+ حالة اختبار لكل جانب من جوانب DataQualityScorer:
اكتمال، صحة أسعار، تناسق، حداثة، شذوذ إحصائي، تصنيف حرفي.
"""
from __future__ import annotations

import pandas as pd
import pytest
from datetime import datetime, timedelta

from services.monitoring.data_quality import (
    DataQualityScorer,
    QualityIssue,
    QualityScore,
)


# ════════════════════════════════════════════════════════════════════
#  تجهيزات مشتركة
# ════════════════════════════════════════════════════════════════════

@pytest.fixture
def scorer() -> DataQualityScorer:
    """إنشاء مُقيِّم جديد لكل اختبار."""
    return DataQualityScorer()


@pytest.fixture
def perfect_df() -> pd.DataFrame:
    """بيانات مثالية بدون أي مشاكل."""
    return pd.DataFrame({
        "المنتج": ["عطر ورد طائفي", "عطر عود كمبودي", "عطر مسك أبيض"],
        "السعر": [150.0, 250.0, 180.0],
        "الماركة": ["العربية للعود", "الحرمين", "رصاصي"],
    })


@pytest.fixture
def empty_df() -> pd.DataFrame:
    """DataFrame فارغ."""
    return pd.DataFrame()


@pytest.fixture
def mixed_df() -> pd.DataFrame:
    """بيانات مختلطة: بعضها صالح وبعضها فيه مشاكل."""
    return pd.DataFrame({
        "المنتج": ["عطر ورد", "", "عطر مسك", None, "عطر عنبر"],
        "السعر": [100.0, 0.0, -5.0, 200.0, 50.0],
        "الماركة": ["العربية", None, "رصاصي", "الحرمين", ""],
    })


# ════════════════════════════════════════════════════════════════════
#  الاختبارات
# ════════════════════════════════════════════════════════════════════

class TestScoreReturnsQualityScore:
    """score() يعيد QualityScore بكل الحقول."""

    def test_returns_quality_score_with_all_fields(
        self, scorer: DataQualityScorer, perfect_df: pd.DataFrame
    ) -> None:
        result = scorer.score(perfect_df)

        assert isinstance(result, QualityScore)
        assert isinstance(result.overall, float)
        assert isinstance(result.completeness, float)
        assert isinstance(result.consistency, float)
        assert isinstance(result.price_validity, float)
        assert isinstance(result.freshness, float)
        assert isinstance(result.issues, list)
        assert isinstance(result.total_rows, int)
        assert isinstance(result.grade, str)
        assert result.total_rows == 3


class TestPerfectData:
    """بيانات مثالية تعطي درجة عالية."""

    def test_perfect_data_gives_high_score(
        self, scorer: DataQualityScorer, perfect_df: pd.DataFrame
    ) -> None:
        result = scorer.score(perfect_df)

        assert result.overall > 90, f"الدرجة الإجمالية {result.overall} يجب أن تكون > 90"
        assert result.completeness > 90
        assert result.price_validity > 90
        assert result.grade == "A"


class TestEmptyDataFrame:
    """DataFrame فارغ يعطي درجة منخفضة."""

    def test_empty_dataframe_gives_low_score(
        self, scorer: DataQualityScorer, empty_df: pd.DataFrame
    ) -> None:
        result = scorer.score(empty_df)

        assert result.overall == 0.0
        assert result.total_rows == 0
        assert len(result.issues) > 0
        assert result.grade == "F"


class TestMissingRequiredColumns:
    """كشف الأعمدة المطلوبة المفقودة."""

    def test_missing_required_columns_detected(
        self, scorer: DataQualityScorer
    ) -> None:
        df = pd.DataFrame({"عمود_عشوائي": [1, 2, 3]})
        result = scorer.score(df)

        # يجب أن تكون هناك مشاكل من فئة missing_field
        missing_issues = [
            i for i in result.issues if i.category == "missing_field"
        ]
        assert len(missing_issues) > 0

        # يجب أن تشير إلى الأعمدة المفقودة
        messages = " ".join(i.message for i in missing_issues)
        assert "المنتج" in messages or "السعر" in messages or "مفقود" in messages


class TestZeroPrices:
    """كشف الأسعار الصفرية."""

    def test_zero_prices_detected_as_issues(
        self, scorer: DataQualityScorer
    ) -> None:
        df = pd.DataFrame({
            "المنتج": ["عطر أ", "عطر ب", "عطر ج"],
            "السعر": [0.0, 100.0, 0.0],
            "الماركة": ["ماركة1", "ماركة2", "ماركة3"],
        })

        _, issues = scorer.check_price_validity(df)
        zero_issues = [i for i in issues if "صفر" in i.message]
        assert len(zero_issues) > 0
        assert zero_issues[0].severity == "error"
        assert zero_issues[0].affected_rows == 2


class TestNegativePrices:
    """كشف الأسعار السلبية."""

    def test_negative_prices_detected_as_issues(
        self, scorer: DataQualityScorer
    ) -> None:
        df = pd.DataFrame({
            "المنتج": ["عطر أ", "عطر ب"],
            "السعر": [-10.0, -50.0],
            "الماركة": ["ماركة1", "ماركة2"],
        })

        _, issues = scorer.check_price_validity(df)
        neg_issues = [i for i in issues if "سلبي" in i.message]
        assert len(neg_issues) > 0
        assert neg_issues[0].severity == "error"
        assert neg_issues[0].affected_rows == 2


class TestExtremePrices:
    """كشف الأسعار المرتفعة جداً (> 10000)."""

    def test_extreme_prices_detected_as_warnings(
        self, scorer: DataQualityScorer
    ) -> None:
        df = pd.DataFrame({
            "المنتج": ["عطر أ", "عطر ب", "عطر ج"],
            "السعر": [100.0, 15000.0, 50000.0],
            "الماركة": ["ماركة1", "ماركة2", "ماركة3"],
        })

        _, issues = scorer.check_price_validity(df)
        high_issues = [i for i in issues if "أعلى" in i.message or "10,000" in i.message]
        assert len(high_issues) > 0
        assert high_issues[0].severity == "warning"
        assert high_issues[0].affected_rows == 2


class TestSuspiciouslyLowPrices:
    """كشف الأسعار المنخفضة بشكل مريب (< 5)."""

    def test_suspiciously_low_prices_detected_as_warnings(
        self, scorer: DataQualityScorer
    ) -> None:
        df = pd.DataFrame({
            "المنتج": ["عطر أ", "عطر ب", "عطر ج"],
            "السعر": [1.0, 3.0, 200.0],
            "الماركة": ["ماركة1", "ماركة2", "ماركة3"],
        })

        _, issues = scorer.check_price_validity(df)
        low_issues = [i for i in issues if "منخفض" in i.message or "أقل" in i.message]
        assert len(low_issues) > 0
        assert low_issues[0].severity == "warning"
        assert low_issues[0].affected_rows == 2


class TestGradeProperty:
    """خاصية grade تعيد التصنيف الحرفي الصحيح."""

    @pytest.mark.parametrize(
        "overall, expected_grade",
        [
            (95.0, "A"),
            (90.0, "A"),
            (89.9, "B"),
            (75.0, "B"),
            (74.9, "C"),
            (60.0, "C"),
            (59.9, "D"),
            (40.0, "D"),
            (39.9, "F"),
            (0.0, "F"),
        ],
    )
    def test_grade_returns_correct_grade(
        self, overall: float, expected_grade: str
    ) -> None:
        qs = QualityScore(overall=overall)
        assert qs.grade == expected_grade, (
            f"overall={overall} → grade='{qs.grade}' (متوقع '{expected_grade}')"
        )


class TestDetectPriceOutliers:
    """كشف الأسعار الشاذة إحصائياً."""

    def test_detect_price_outliers_finds_statistical_outliers(
        self, scorer: DataQualityScorer
    ) -> None:
        # أسعار طبيعية مع قيمة شاذة واحدة
        normal_prices = [100.0, 105.0, 98.0, 102.0, 99.0, 101.0, 103.0, 97.0, 104.0, 100.0]
        outlier_price = [5000.0]  # شاذة جداً

        df = pd.DataFrame({
            "المنتج": [f"عطر {i}" for i in range(11)],
            "السعر": normal_prices + outlier_price,
            "الماركة": [f"ماركة{i}" for i in range(11)],
        })

        issues = scorer.detect_price_outliers(df)
        assert len(issues) > 0
        assert issues[0].category == "outlier_price"
        assert issues[0].affected_rows >= 1
        assert "5000" in str(issues[0].sample_values)


class TestCheckCompleteness:
    """فحص الاكتمال يعدّ الخلايا الفارغة بدقة."""

    def test_check_completeness_counts_empty_cells_correctly(
        self, scorer: DataQualityScorer
    ) -> None:
        df = pd.DataFrame({
            "المنتج": ["عطر أ", "", None, "عطر د"],
            "السعر": [100.0, 200.0, 300.0, 400.0],
            "الماركة": ["ماركة1", "ماركة2", "", None],
        })

        score, issues = scorer.check_completeness(df)
        assert score < 100.0

        empty_issues = [
            i for i in issues
            if i.category == "missing_field" and "فارغة" in i.message
        ]
        assert len(empty_issues) > 0

        # عمود المنتج فيه 2 فارغة، عمود الماركة فيه 2 فارغة
        product_issue = [i for i in empty_issues if "المنتج" in i.message]
        brand_issue = [i for i in empty_issues if "الماركة" in i.message]
        assert len(product_issue) > 0
        assert product_issue[0].affected_rows == 2
        assert len(brand_issue) > 0
        assert brand_issue[0].affected_rows == 2


class TestMixedValidInvalidData:
    """بيانات مختلطة تعطي درجة متوسطة."""

    def test_mixed_valid_invalid_data_gives_mid_range_score(
        self, scorer: DataQualityScorer, mixed_df: pd.DataFrame
    ) -> None:
        result = scorer.score(mixed_df)

        # يجب أن تكون بين 20 و 80 (ليست مثالية وليست كارثية)
        assert 10.0 < result.overall < 85.0, (
            f"الدرجة {result.overall} يجب أن تكون في النطاق المتوسط"
        )
        assert len(result.issues) > 0
        assert result.grade in ("B", "C", "D", "F")


class TestFreshnessNoDateColumns:
    """الحداثة تعيد 100 عند عدم وجود أعمدة تاريخ."""

    def test_freshness_returns_100_when_no_date_columns(
        self, scorer: DataQualityScorer, perfect_df: pd.DataFrame
    ) -> None:
        freshness, issues = scorer.check_freshness(perfect_df)
        assert freshness == 100.0
        assert len(issues) == 0


class TestFreshnessWithStaleDates:
    """الحداثة تكشف البيانات القديمة."""

    def test_freshness_detects_stale_data(
        self, scorer: DataQualityScorer
    ) -> None:
        old_date = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
        df = pd.DataFrame({
            "المنتج": ["عطر أ"],
            "السعر": [100.0],
            "الماركة": ["ماركة1"],
            "التاريخ": [old_date],
        })

        freshness, issues = scorer.check_freshness(df)
        assert freshness == 0.0
        assert len(issues) > 0
        assert issues[0].category == "stale_data"


class TestQualityIssueDefaults:
    """QualityIssue يُنشأ بقيم افتراضية سليمة."""

    def test_quality_issue_has_unique_id(self) -> None:
        issue1 = QualityIssue()
        issue2 = QualityIssue()
        assert issue1.issue_id != issue2.issue_id
        assert len(issue1.issue_id) == 8

    def test_quality_issue_default_sample_values(self) -> None:
        issue = QualityIssue()
        assert issue.sample_values == []
        assert issue.affected_rows == 0
        assert issue.severity == "warning"
