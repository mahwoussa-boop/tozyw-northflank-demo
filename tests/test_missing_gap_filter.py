# -*- coding: utf-8 -*-
"""tests/test_missing_gap_filter.py — فلتر «نوع الفجوة» في صفحة المفقودات.

الخلفية (تدقيق 2026-07-26): «مؤكد مفقود» كان يخلط منتجاً جديداً كلياً بنسخة
حجم/جنس لما نملكه. المحرّك يكتب التمييز في عمود ``سبب_النقل`` منذ البداية —
5,129 صفاً «نسخة حجم غير متوفرة لدينا — مطابقة 100%» + 1,403 بمطابقة 83–88%
+ 705 «نسخة جنس مختلفة» — لكنه لم يكن يُعرَض ولا يُفلتَر به.
"""
import pandas as pd

from ui.pages.missing import (
    _GAP_ALL, _GAP_GENDER, _GAP_NEW, _GAP_SIZE,
    filter_by_gap, gap_counts, gap_type,
)


def _df() -> pd.DataFrame:
    return pd.DataFrame({
        "منتج_المنافس": ["جديد أ", "حجم ب", "جنس ج", "جديد د", "حجم هـ"],
        "سبب_النقل": [
            "تشابه اسمي عابر للماركات",
            "نسخة حجم غير متوفرة لدينا — مطابقة 100%",
            "نسخة جنس مختلفة",
            None,
            "نسخة حجم غير متوفرة لدينا — مطابقة 83%",
        ],
    })


def test_gap_type_reads_engine_reason():
    rows = list(_df().to_dict("records"))
    assert gap_type(rows[0]) == "new"
    assert gap_type(rows[1]) == "size"
    assert gap_type(rows[2]) == "gender"
    assert gap_type(rows[3]) == "new"      # سبب فارغ ⇒ يُعامَل كجديد
    assert gap_type(rows[4]) == "size"     # نسبة المطابقة لا تغيّر النوع


def test_gap_type_safe_on_junk():
    assert gap_type(None) == "new"
    assert gap_type("نصّ لا قاموس") == "new"


def test_gap_counts():
    assert gap_counts(_df()) == (2, 2, 1)


def test_gap_counts_missing_column():
    df = pd.DataFrame({"منتج_المنافس": ["أ", "ب"]})
    assert gap_counts(df) == (2, 0, 0)     # بلا العمود: الكل «جديد»
    assert gap_counts(pd.DataFrame()) == (0, 0, 0)


def test_filter_by_gap_splits_correctly():
    df = _df()
    assert len(filter_by_gap(df, _GAP_ALL)) == 5
    assert filter_by_gap(df, _GAP_NEW)["منتج_المنافس"].tolist() == ["جديد أ", "جديد د"]
    assert filter_by_gap(df, _GAP_SIZE)["منتج_المنافس"].tolist() == ["حجم ب", "حجم هـ"]
    assert filter_by_gap(df, _GAP_GENDER)["منتج_المنافس"].tolist() == ["جنس ج"]


def test_filter_conserves_rows():
    """قانون الحفظ: جديد + حجم + جنس = الكل (لا صفّ يضيع بين الفلاتر)."""
    df = _df()
    total = sum(
        len(filter_by_gap(df, c)) for c in (_GAP_NEW, _GAP_SIZE, _GAP_GENDER)
    )
    assert total == len(df)


def test_filter_safe_without_column_or_empty():
    df = pd.DataFrame({"منتج_المنافس": ["أ", "ب"]})
    assert len(filter_by_gap(df, _GAP_SIZE)) == 2   # لا عمود ⇒ لا تصفية (لا إخفاء صامت)
    assert filter_by_gap(pd.DataFrame(), _GAP_NEW).empty
    assert filter_by_gap(None, _GAP_NEW).empty
