# -*- coding: utf-8 -*-
"""اختبارات services/reclassify_missing.py — قاعدة لكل حالة + حدّيات.

الدالة نقية وغير موصولة بأي مسار حي؛ الاختبارات صفوف dict صرفة بلا قاعدة/Streamlit.
"""
import pandas as pd
import pytest

from services.reclassify_missing import (
    REASON_GENDER,
    REASON_SIZE,
    brands_differ,
    bucket_of,
    build_our_brand_index,
    propose,
)


def _row(**over):
    base = {
        "مستوى_الثقة": "review",
        "درجة_التشابه": 90.0,
        "حالة_المراجعة": "بانتظار التحقق",
        "الماركة": "شانيل",
        "منتج_مطابق_محتمل": "منتجنا س",
        "عدد_المنافسين": 3,
    }
    base.update(over)
    return base


OUR_BRANDS = {"منتجنا س": "ديور", "منتجنا ص": "شانيل"}


# ── green passthrough ──────────────────────────────────────────────────────
def test_green_passthrough_untouched():
    row = _row(مستوى_الثقة="green", حالة_المراجعة=REASON_SIZE)
    assert propose(row, OUR_BRANDS) == ("green", "", 0.0)
    assert bucket_of(row, propose(row, OUR_BRANDS)) == "green-passthrough"


# ── R1: فرق حجم بدرجة ≥ 82 ─────────────────────────────────────────────────
def test_r1_size_reason_high_score():
    level, reason, prio = propose(_row(حالة_المراجعة=REASON_SIZE, درجة_التشابه=95.0))
    assert level == "green" and "نسخة حجم" in reason and "95%" in reason
    assert prio == 95.0


def test_r1_boundary_exactly_82():
    level, reason, _ = propose(_row(حالة_المراجعة=REASON_SIZE, درجة_التشابه=82.0))
    assert level == "green" and "82%" in reason


def test_r1_below_threshold_falls_to_r4():
    row = _row(حالة_المراجعة=REASON_SIZE, درجة_التشابه=81.9)
    # 81.9 < 82 لكن ≥ 65: يمرّ على R3 — ماركة المطابق (ديور) ≠ شانيل ⇒ R3
    assert bucket_of(row, propose(row, OUR_BRANDS)) == "R3"
    # وبلا فهرس ماركات ⇒ R4
    level, reason, prio = propose(row, None)
    assert (level, reason, prio) == ("review", "", 3.0)


# ── R2: فرق جنس بدرجة ≥ 82 ─────────────────────────────────────────────────
def test_r2_gender_reason():
    result = propose(_row(حالة_المراجعة=REASON_GENDER, درجة_التشابه=88.0))
    assert result == ("green", "نسخة جنس مختلفة", 88.0)
    assert bucket_of(_row(حالة_المراجعة=REASON_GENDER), result) == "R2"


def test_r2_low_score_gender_falls_to_r4():
    row = _row(حالة_المراجعة=REASON_GENDER, درجة_التشابه=70.0, الماركة="شانيل",
               منتج_مطابق_محتمل="منتجنا ص")  # ماركة المطابق شانيل = نفسها ⇒ لا R3
    assert propose(row, OUR_BRANDS) == ("review", "", 3.0)


# ── R3: تشابه اسمي عابر للماركات (65 ≤ score < 82) ─────────────────────────
def test_r3_cross_brand():
    row = _row(درجة_التشابه=75.0)  # شانيل مقابل ماركة المطابق ديور
    assert propose(row, OUR_BRANDS) == ("green", "تشابه اسمي عابر للماركات", 75.0)


def test_r3_boundary_exactly_65_fires_and_below_does_not():
    assert propose(_row(درجة_التشابه=65.0), OUR_BRANDS)[0] == "green"
    assert propose(_row(درجة_التشابه=64.9), OUR_BRANDS) == ("review", "", 3.0)


def test_r3_needs_both_brands_present():
    assert propose(_row(درجة_التشابه=75.0, الماركة=""), OUR_BRANDS)[0] == "review"
    row = _row(درجة_التشابه=75.0, منتج_مطابق_محتمل="غير موجود في الفهرس")
    assert propose(row, OUR_BRANDS)[0] == "review"


def test_r3_same_brand_bilingual_not_moved():
    ours = {"منتجنا س": "نيكولاي NICOLAI"}
    row = _row(درجة_التشابه=75.0, الماركة="نيكولاي")
    assert propose(row, ours) == ("review", "", 3.0)  # رمز مشترك ⇒ ليستا مختلفتين


def test_score_82_without_size_or_gender_reason_is_r4():
    # score ≥ 82 وسبب «بانتظار التحقق» ⇒ لا R1/R2، وخارج نطاق R3 ⇒ R4
    assert propose(_row(درجة_التشابه=82.0), OUR_BRANDS) == ("review", "", 3.0)


# ── R4: الأولوية = عدد المنافسين، ومرونة القيم ─────────────────────────────
def test_r4_priority_is_competitor_count():
    assert propose(_row(درجة_التشابه=50.0, عدد_المنافسين=7))[2] == 7.0
    assert propose(_row(درجة_التشابه=50.0, عدد_المنافسين="tricky"))[2] == 0.0


def test_bad_score_treated_as_zero():
    assert propose(_row(درجة_التشابه="ليس رقماً")) == ("review", "", 3.0)


# ── brands_differ / build_our_brand_index ──────────────────────────────────
def test_brands_differ_placeholders_are_empty():
    assert not brands_differ("غير محدد", "ديور")
    assert not brands_differ("", "ديور")
    assert brands_differ("شانيل", "ديور")


def test_build_our_brand_index():
    df = pd.DataFrame({
        "المنتج": ["منتج 1", "منتج 2", "منتج 3", ""],
        "الماركة": ["ديور", "غير محدد", "شانيل", "أرماني"],
    })
    idx = build_our_brand_index(df)
    assert idx == {"منتج 1": "ديور", "منتج 3": "شانيل"}
    assert build_our_brand_index(None) == {}
    assert build_our_brand_index(pd.DataFrame({"عمود": [1]})) == {}


def test_no_deletion_levels_only():
    for row in (_row(), _row(مستوى_الثقة="green"), _row(درجة_التشابه=99.0,
                حالة_المراجعة=REASON_SIZE)):
        level, _, _ = propose(row, OUR_BRANDS)
        assert level in ("green", "review")


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
