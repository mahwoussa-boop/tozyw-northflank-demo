"""م6 — كونان منفصلان في لوحة التحكم: كتالوجنا مقابل عروض المنافسين.

``kpi_breakdown`` كان صحيحاً أصلاً (يستثني MISSING من المقام ويعيد None لنسبته)؛
العطب **عرضيّ**: بطاقة المفقودات تعرض المؤكَّد وحده (13,693) بينما صفحة
المفقودات تعرض 55,316 (مؤكَّد + محتمل) — رقمان لنفس الشيء في شاشتين.
"""
from __future__ import annotations

import pandas as pd

from core.enums import SectionType
from ui.pages.dashboard import catalog_total, kpi_breakdown, missing_split


class _Result:
    def __init__(self, counts):
        self.section_counts = counts


def _counts():
    """الأعداد المرجعية المقيسة (المجموع 11,516 هو الثابت، لا الأخماس)."""
    return {
        SectionType.PRICE_RAISE: 4_530,
        SectionType.PRICE_LOWER: 903,
        SectionType.APPROVED: 1_535,
        SectionType.REVIEW: 1_724,
        SectionType.EXCLUDED: 2_824,
        SectionType.MISSING: 13_693,
    }


def test_catalog_total_excludes_missing():
    assert catalog_total(_Result(_counts())) == 11_516


def test_catalog_total_is_safe_when_empty():
    assert catalog_total(None) == 0
    assert catalog_total(_Result({})) == 0


def test_missing_split_reports_confirmed_and_possible():
    sections = {"missing_review": pd.DataFrame({"x": range(41_623)})}
    confirmed, possible = missing_split(_Result(_counts()), sections)
    assert (confirmed, possible) == (13_693, 41_623)
    assert confirmed + possible == 55_316


def test_missing_split_without_review_section_reports_zero_possible():
    """غياب القسم لا يكسر الصفحة ولا يخترع رقماً."""
    assert missing_split(_Result(_counts()), None) == (13_693, 0)
    assert missing_split(_Result(_counts()), {}) == (13_693, 0)


def test_missing_is_never_folded_into_the_catalog_denominator():
    """جوهر م6: لا مقام مشترك — المفقود بلا نسبة، والكتالوج نسبته 100%."""
    rows = kpi_breakdown(_Result(_counts()))
    catalog_pcts = [p for label, _c, p in rows if p is not None]
    missing_pcts = [p for label, _c, p in rows if p is None]

    assert len(missing_pcts) == 1, "المفقود يجب أن يبقى بلا نسبة"
    assert round(sum(catalog_pcts), 6) == 100.0, "نِسَب الكتالوج يجب أن تجمع 100%"
