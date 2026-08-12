"""tests/test_classification_service.py — اختبارات التوزيع وحفظ البيانات (P2)."""
import pandas as pd
import pytest

from core.exceptions import DataLossError
from services.classification_service import (
    ClassificationService,
    RECOVER_DECISION,
    recover_uncovered_catalog,
    split_results,
)


def _df(decisions: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"القرار": decisions, "المنتج": [f"p{i}" for i in range(len(decisions))]})


def test_exclusive_distribution_and_conservation() -> None:
    df = _df([
        "🔴 سعر أعلى", "🟢 سعر أقل", "✅ موافق",
        "⚠️ تحت المراجعة", "🔍 منتجات مفقودة", "⚪ مستبعد",
    ])
    result = ClassificationService().classify(df)
    counts = result.counts()
    assert counts["price_raise"] == 1
    assert counts["price_lower"] == 1
    assert counts["approved"] == 1
    assert counts["review"] == 2            # ⚠️ و🔍 كلاهما مراجعة
    assert counts["excluded"] == 1
    assert result.gap == 0 and result.total_in == 6


def test_safety_net_unknown_decision_goes_excluded() -> None:
    df = _df(["🔴 سعر أعلى", "قرار غريب غير معروف", ""])
    result = ClassificationService().classify(df)
    assert result.counts()["excluded"] == 2  # المجهول + الفارغ
    assert result.gap == 0


def test_lower_matches_text_contains() -> None:
    # «سعر أقل» نصاً (بلا 🟢) يجب أن يقع في price_lower (#PRESERVED_LOGIC)
    res = split_results(_df(["مطابقة بسعر أقل من المنافس"]))
    assert len(res["price_lower"]) == 1


def test_empty_dataframe_returns_empty_sections() -> None:
    result = ClassificationService().classify(pd.DataFrame())
    assert result.total_in == 0 and result.gap == 0
    assert all(v == 0 for v in result.counts().values())


def test_strict_raises_on_duplicate_overlap() -> None:
    # قرار يبدأ 🔴 ويحوي «سعر أقل» ⇒ يظهر في قسمين ⇒ خرق حفظ البيانات
    df = _df(["🔴 سعر أقل"])
    with pytest.raises(DataLossError):
        ClassificationService().classify(df, strict=True)


def test_missing_column_is_tolerated() -> None:
    df = pd.DataFrame({"المنتج": ["a", "b"]})  # لا عمود قرار
    result = ClassificationService().classify(df)
    assert result.counts()["excluded"] == 2 and result.gap == 0


# ── شبكة أمان الكتالوج: استرداد المنتجات التي رشّحها المحرّك ──────────────

def _sections(names: list[str]) -> dict[str, pd.DataFrame]:
    """أقسام بحد أدنى: كل الأسماء في price_raise، البقية فارغة (مع عمود الاسم)."""
    cols = ["المنتج", "السعر", "القرار"]
    pr = pd.DataFrame({"المنتج": names, "السعر": [100] * len(names),
                       "القرار": ["🔴 سعر أعلى"] * len(names)})
    empty = pd.DataFrame(columns=cols)
    return {"price_raise": pr, "price_lower": empty.copy(), "approved": empty.copy(),
            "review": empty.copy(), "excluded": empty.copy()}


def test_recover_uncovered_closes_conservation_gap() -> None:
    # الكتالوج 5 منتجات، المحرّك أنتج 3 فقط ⇒ 2 يجب أن يُستردّا إلى «مستبعد».
    our = pd.DataFrame({"المنتج": ["a", "b", "c", "d", "e"], "السعر": [10, 20, 30, 40, 50]})
    sections = _sections(["a", "b", "c"])
    before = sum(len(v) for v in sections.values())
    sections, n = recover_uncovered_catalog(our, sections)
    after = sum(len(v) for v in sections.values())
    assert n == 2
    assert after - before == 2
    assert after == len(our)                       # total_in == Σ(الأقسام)
    rec = sections["excluded"]
    assert set(rec["المنتج"]) == {"d", "e"}
    assert (rec["القرار"] == RECOVER_DECISION).all()
    assert set(rec["السعر"]) == {40, 50}           # السعر منقول من الكتالوج


def test_recover_is_noop_when_fully_covered() -> None:
    our = pd.DataFrame({"المنتج": ["a", "b"], "السعر": [10, 20]})
    sections = _sections(["a", "b"])
    sections, n = recover_uncovered_catalog(our, sections)
    assert n == 0 and len(sections["excluded"]) == 0


def test_recover_dedupes_by_name_and_skips_blanks() -> None:
    # اسم مكرّر غير مُغطّى يُستردّ مرة واحدة؛ الفراغ/NaN يُتجاهَل.
    our = pd.DataFrame({"المنتج": ["x", "x", "", "nan"], "السعر": [1, 1, 0, 0]})
    sections = _sections(["a"])
    sections, n = recover_uncovered_catalog(our, sections)
    assert n == 1 and list(sections["excluded"]["المنتج"]) == ["x"]


def test_recover_handles_empty_inputs() -> None:
    assert recover_uncovered_catalog(None, _sections([]))[1] == 0
    assert recover_uncovered_catalog(pd.DataFrame(), _sections([]))[1] == 0
