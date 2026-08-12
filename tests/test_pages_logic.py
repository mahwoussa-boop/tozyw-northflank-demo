"""tests/test_pages_logic.py — اختبارات منطق الصفحات الخالص (P4) دون Streamlit."""
import pytest
import pandas as pd

from core.enums import SectionType
from core.models import AnalysisResult, ReconciliationReport
from ui.pages._section_page import section_dataframe
from ui.pages.dashboard import (
    conservation_status,
    kpi_breakdown,
    kpi_metrics,
    top_urgent_products,
)
from ui.pages.missing import split_missing
from ui.pages.processed import processed_rows
from ui.state_manager import AppState


def test_section_dataframe_extracts_by_enum_value() -> None:
    sections = {"price_raise": pd.DataFrame({"x": [1, 2]}), "approved": pd.DataFrame()}
    assert len(section_dataframe(sections, SectionType.PRICE_RAISE)) == 2
    assert section_dataframe(sections, SectionType.MISSING).empty  # مفتاح غائب


def test_conservation_status_states() -> None:
    assert conservation_status(None)[0] is True
    ok = ReconciliationReport.from_sections(
        10, {"price_raise": 4, "price_lower": 3, "approved": 1,
             "review": 1, "excluded": 1})
    assert conservation_status(ok)[0] is True
    bad = ReconciliationReport.from_sections(
        10, {"price_raise": 4, "price_lower": 3, "approved": 1,
             "review": 1, "excluded": 0})
    status, text = conservation_status(bad)
    assert status is False and "فجوة=1" in text


def test_kpi_metrics() -> None:
    result = AnalysisResult(section_counts={
        SectionType.PRICE_RAISE: 5, SectionType.MISSING: 3})
    metrics = kpi_metrics(result)
    assert ("🔴 سعر أعلى", 5) in metrics and ("🔍 منتجات مفقودة", 3) in metrics
    assert kpi_metrics(None) == []


def _real_counts() -> dict:
    """أعداد آخر تحليل حقيقي (2026-07-19) — كتالوج 11,518 + 26,129 مفقوداً."""
    return {
        SectionType.PRICE_RAISE: 4766, SectionType.PRICE_LOWER: 905,
        SectionType.APPROVED: 1760, SectionType.REVIEW: 976,
        SectionType.EXCLUDED: 3111, SectionType.MISSING: 26129,
    }


def test_kpi_breakdown_percentages_are_of_our_catalog_not_mixed_base() -> None:
    """«مفقود» منتجات منافسين لا نملكها؛ جمعها في المقام كان يُصغّر كل نسبة ~3.3×
    (رفع سعر بدا 12.7% وحقيقته 41.4% من الكتالوج)."""
    rows = kpi_breakdown(AnalysisResult(section_counts=_real_counts()))
    pcts = {label: pct for label, _c, pct in rows}
    assert pcts["🔴 سعر أعلى"] == pytest.approx(41.4, abs=0.1)
    assert pcts["⚪ مستبعد (لا يوجد تطابق)"] == pytest.approx(27.0, abs=0.1)


def test_kpi_breakdown_catalog_percentages_sum_to_100() -> None:
    """حارس المعنى: نِسَب أقسام الكتالوج توزيعٌ كامل، لا شرائح من كون مختلط."""
    rows = kpi_breakdown(AnalysisResult(section_counts=_real_counts()))
    total = sum(pct for _l, _c, pct in rows if pct is not None)
    assert total == pytest.approx(100.0, abs=0.05)


def test_kpi_breakdown_missing_has_no_percentage() -> None:
    """المفقود يُعرض بعدده بلا نسبة — قاعدته كون آخر فلا نسبة له معنى."""
    rows = kpi_breakdown(AnalysisResult(section_counts=_real_counts()))
    missing = [(c, p) for label, c, p in rows if label == "🔍 منتجات مفقودة"]
    assert missing == [(26129, None)]


def test_kpi_breakdown_empty_result_is_safe() -> None:
    assert kpi_breakdown(None) == []
    assert kpi_breakdown(AnalysisResult(section_counts={})) == []


def test_kpi_breakdown_missing_only_does_not_divide_by_zero() -> None:
    """كتالوج فارغ ومفقودات فقط: لا انهيار ولا نسبة مختلقة."""
    rows = kpi_breakdown(AnalysisResult(section_counts={SectionType.MISSING: 7}))
    assert rows == [("🔍 منتجات مفقودة", 7, None)]


def test_top_urgent_products_sorted_by_gap() -> None:
    # «سعر أقل» = سعرنا أقل من المنافس = فرصة رفع. الفجوة = المنافس − سعرنا.
    sections = {
        "price_lower": pd.DataFrame({
            "المنتج": ["a", "b", "c", "d"],
            "السعر": [100, 100, 100, 100],
            "سعر_المنافس": [150, 300, 120, 80],  # d: المنافس أرخص ⇒ يُستبعد
            "المنافس": ["s1", "s2", "s3", "s4"],
        }),
    }
    top = top_urgent_products(sections, n=2)
    assert [r["name"] for r in top] == ["b", "a"]   # أكبر فجوة أولاً (200 ثم 50)
    assert top[0]["gap"] == 200.0 and top[0]["comp_price"] == 300.0
    # d مُستبعد (المنافس 80 < سعرنا 100 ⇒ لا فرصة رفع)
    assert "d" not in [r["name"] for r in top_urgent_products(sections, n=10)]
    # حالات حدية: لا أقسام / قسم غائب / أعمدة ناقصة ⇒ قائمة فارغة
    assert top_urgent_products(None) == []
    assert top_urgent_products({"price_lower": pd.DataFrame({"المنتج": ["x"]})}) == []


def test_split_missing() -> None:
    df = pd.DataFrame({
        "منتج_المنافس": ["a", "b", "c"],
        "مستوى_الثقة": ["green", "review", "green"],
    })
    green, review = split_missing(df)
    assert len(green) == 2 and len(review) == 1
    # عمود غائب ⇒ الكل يُعاد كـ green-جانب والمراجعة فارغة
    g2, r2 = split_missing(pd.DataFrame({"منتج_المنافس": ["x"]}))
    assert len(g2) == 1 and r2.empty


def test_processed_rows_sorted_and_capped() -> None:
    state = AppState()
    for sku in ("c", "a", "b"):
        state.mark_price_processed(sku)
    assert processed_rows(state) == ["a", "b", "c"]
    assert processed_rows(state, limit=2) == ["a", "b"]
