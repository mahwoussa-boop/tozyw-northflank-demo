# -*- coding: utf-8 -*-
"""اختبارات ترتيب «الأهم أولاً» (ui/components/criticality) — طلب المالك.

الأهمية = عدد المنافسين الأرخص منّا (سعرنا مرتفع نسبةً للسوق = نخسر = أهمّ).
دوال نقية بلا Streamlit."""
import json

import pandas as pd

from conf.constants import COL_OUR_PRICE, COL_COMP_DETAILS
from ui.components.criticality import competitors_below_count, priority_sorted


def _row(our_price, comp_prices, tag=""):
    details = [{"المنافس": f"m{i}", "السعر": p} for i, p in enumerate(comp_prices)]
    return {
        COL_OUR_PRICE: our_price,
        COL_COMP_DETAILS: json.dumps(details, ensure_ascii=False),
        "_tag": tag,
    }


def test_below_count_basic():
    """سعرنا 100؛ منافسون 80/90/120 ⇒ اثنان أرخص منّا."""
    assert competitors_below_count(_row(100, [80, 90, 120])) == 2


def test_below_count_none_below():
    assert competitors_below_count(_row(100, [110, 120, 130])) == 0


def test_below_count_no_price_or_details_safe():
    assert competitors_below_count(_row(0, [80, 90])) == 0          # سعرنا مجهول
    assert competitors_below_count({COL_OUR_PRICE: 100}) == 0       # بلا تفاصيل منافسين
    assert competitors_below_count({}) == 0                          # صفّ فارغ


def test_priority_sorted_orders_by_loss_desc():
    """الأكثر منافسين أرخص منّا يظهر أولاً."""
    df = pd.DataFrame([
        _row(100, [90, 120], tag="a2"),           # 1 أرخص
        _row(100, [], tag="b0"),                  # 0
        _row(100, [70, 60, 50, 40], tag="c4"),    # 4 أرخص ⇒ الأهم
        _row(100, [80, 90, 95], tag="d3"),        # 3 أرخص
    ])
    out = priority_sorted(df)
    assert list(out["_tag"]) == ["c4", "d3", "a2", "b0"]


def test_priority_sorted_stable_on_ties():
    """عند تساوي الأهمية يحافظ على الترتيب الأصلي (stable)."""
    df = pd.DataFrame([
        _row(100, [90], tag="first"),
        _row(100, [80], tag="second"),
    ])
    out = priority_sorted(df)
    assert list(out["_tag"]) == ["first", "second"]


def test_priority_sorted_empty_safe():
    assert priority_sorted(pd.DataFrame()).empty
    out = priority_sorted(pd.DataFrame({COL_OUR_PRICE: [100]}))  # بلا عمود تفاصيل
    assert len(out) == 1  # لا يكسر، يعيد الصف


def test_priority_sorted_does_not_mutate_source():
    df = pd.DataFrame([_row(100, [80]), _row(100, [])])
    before = df.copy()
    priority_sorted(df)
    assert df.equals(before)  # المصدر لم يتغيّر (عرض فقط)
