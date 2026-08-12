"""tests/test_missing_priority.py — طبقة أولوية «للقراءة فقط» في المفقودات.

smart_sort يرتّب المفقودات بالحضور (عدد المنافسين) تنازلياً كمفتاح أولوية أساسي،
ثم الثقة ثم السعر. آمن: غياب مصدر الحضور ⇒ الفرز القديم. لا يمسّ بيانات المصدر.
_presence_badge ينتج نص شارة العرض «⚡ موجود عند N منافسين» (حضور ≥2).
"""
import pandas as pd

from engines.mahally_scraper import MahallyScraper
from ui.pages.missing import _PRESENCE_COL, _presence_badge, smart_sort

_CONF = "مستوى_الثقة"
_norm = MahallyScraper.normalize


def _df(rows):
    return pd.DataFrame(rows)


def test_higher_presence_ranks_first():
    df = _df([
        {_CONF: "review", "منتج_المنافس": "عطر أ", "سعر_المنافس": 100, "عدد_المنافسين": 1},
        {_CONF: "review", "منتج_المنافس": "عطر ب", "سعر_المنافس": 100, "عدد_المنافسين": 5},
    ])
    pres = {_norm("عطر أ"): 1, _norm("عطر ب"): 5}
    out = smart_sort(df, presence=pres)
    assert list(out["منتج_المنافس"]) == ["عطر ب", "عطر أ"]  # 5 يتصدّر 1
    assert list(out[_PRESENCE_COL]) == [5, 1]


def test_no_presence_source_keeps_old_confidence_price_order():
    df = _df([
        {_CONF: "green",  "منتج_المنافس": "عطر أ", "سعر_المنافس": 100, "عدد_المنافسين": 1},
        {_CONF: "review", "منتج_المنافس": "عطر ب", "سعر_المنافس": 50,  "عدد_المنافسين": 9},
    ])
    out = smart_sort(df, presence={})  # مصدر حضور فارغ ⇒ الفرز القديم (ثقة ثم سعر)
    assert list(out["منتج_المنافس"]) == ["عطر أ", "عطر ب"]  # green أولاً رغم حضور ب
    assert _PRESENCE_COL not in out.columns


def test_unmatched_row_falls_back_to_aggregate_count():
    df = _df([
        {_CONF: "review", "منتج_المنافس": "عطر مجهول", "سعر_المنافس": 100, "عدد_المنافسين": 3},
        {_CONF: "review", "منتج_المنافس": "عطر معروف", "سعر_المنافس": 100, "عدد_المنافسين": 1},
    ])
    pres = {_norm("عطر معروف"): 1}  # «مجهول» غير مطابق ⇒ احتياطي عدد_المنافسين=3
    out = smart_sort(df, presence=pres)
    assert list(out["منتج_المنافس"]) == ["عطر مجهول", "عطر معروف"]  # 3 (احتياطي) > 1
    assert list(out[_PRESENCE_COL]) == [3, 1]


def test_presence_primary_over_confidence():
    # الحضور مفتاح أولوية أساسي: منتج review بحضور عالٍ يتصدّر green بحضور منخفض.
    df = _df([
        {_CONF: "green",  "منتج_المنافس": "عطر أخضر", "سعر_المنافس": 100, "عدد_المنافسين": 1},
        {_CONF: "review", "منتج_المنافس": "عطر مطلوب", "سعر_المنافس": 100, "عدد_المنافسين": 5},
    ])
    pres = {_norm("عطر أخضر"): 1, _norm("عطر مطلوب"): 5}
    out = smart_sort(df, presence=pres)
    assert list(out["منتج_المنافس"]) == ["عطر مطلوب", "عطر أخضر"]


def test_empty_or_missing_conf_column_safe():
    assert smart_sort(pd.DataFrame()).empty
    df = _df([{"منتج_المنافس": "x", "عدد_المنافسين": 5}])  # لا عمود ثقة
    out = smart_sort(df, presence={_norm("x"): 5})
    assert len(out) == 1  # لا يكسر


def test_badge_text():
    assert _presence_badge(5) == "⚡ موجود عند 5 منافسين"
    assert _presence_badge(2) == "⚡ موجود عند 2 منافسين"
    assert _presence_badge(1) == ""   # حضور 1 = خطّ الأساس، لا شارة
    assert _presence_badge(0) == ""
    assert _presence_badge(None) == ""


# ── فرز «الأحدث رصداً» + شارة 🆕 (الطلب 2) ─────────────────────────────
from datetime import datetime, timedelta

from ui.pages.missing import _days_since_seen, _new_badge, sort_newest

_FS = "تاريخ_أول_رصد"


def _dt(days_ago):
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


def test_sort_newest_descending():
    df = _df([
        {_CONF: "green", "منتج_المنافس": "قديم", _FS: _dt(40)},
        {_CONF: "green", "منتج_المنافس": "اليوم", _FS: _dt(0)},
        {_CONF: "green", "منتج_المنافس": "اسبوع", _FS: _dt(5)},
    ])
    out = sort_newest(df)
    assert list(out["منتج_المنافس"]) == ["اليوم", "اسبوع", "قديم"]


def test_sort_newest_empty_date_last():
    df = _df([
        {_CONF: "green", "منتج_المنافس": "بلا تاريخ", _FS: ""},
        {_CONF: "green", "منتج_المنافس": "حديث", _FS: _dt(1)},
    ])
    out = sort_newest(df)
    assert list(out["منتج_المنافس"]) == ["حديث", "بلا تاريخ"]  # الفارغ للأسفل


def test_sort_newest_missing_column_falls_back():
    df = _df([{_CONF: "green", "منتج_المنافس": "x", "عدد_المنافسين": 1}])
    out = sort_newest(df)  # لا عمود تاريخ ⇒ smart_sort بلا كسر
    assert len(out) == 1


def test_new_badge_window():
    assert _new_badge(pd.Series({_FS: _dt(0)})) == "🆕 جديد اليوم"
    assert _new_badge(pd.Series({_FS: _dt(3)})) == "🆕 جديد — رُصد قبل 3 يوم"
    assert _new_badge(pd.Series({_FS: _dt(7)})).startswith("🆕")   # الحدّ ضمن النافذة
    assert _new_badge(pd.Series({_FS: _dt(8)})) == ""             # خارج النافذة
    assert _new_badge(pd.Series({_FS: ""})) == ""                 # فارغ آمن


def test_days_since_seen_parsing():
    assert _days_since_seen(_dt(5)) == 5
    assert _days_since_seen("") is None
    assert _days_since_seen("نص غير صالح") is None
    assert _days_since_seen("2026-07-01") is not None  # تاريخ فقط بلا وقت


# ── فرز «الأكثر طلباً (تقييماً)» — الأكثر تقييماً وليس عندك ──────────────
from engines.mahally_scraper import MahallyScraper as _MS
from ui.pages.missing import sort_most_rated


def test_sort_most_rated_by_market_total(monkeypatch):
    import ui.pages.missing as mp
    k_hi = _MS.normalize("عطر مطلوب")
    k_lo = _MS.normalize("عطر عادي")
    monkeypatch.setattr(mp, "_topcomp_loaded", True, raising=False)
    monkeypatch.setattr(mp, "_topcomp_cache", {
        k_hi: {"total_rating_count": 900},
        k_lo: {"total_rating_count": 30},
    }, raising=False)
    df = _df([
        {_CONF: "green", "منتج_المنافس": "عطر عادي"},
        {_CONF: "green", "منتج_المنافس": "عطر مطلوب"},
        {_CONF: "green", "منتج_المنافس": "عطر بلا تقييم"},  # غير موجود بالكاش ⇒ 0
    ])
    out = sort_most_rated(df)
    assert list(out["منتج_المنافس"]) == ["عطر مطلوب", "عطر عادي", "عطر بلا تقييم"]


def test_sort_most_rated_no_cache_falls_back(monkeypatch):
    import ui.pages.missing as mp
    monkeypatch.setattr(mp, "_topcomp_loaded", True, raising=False)
    monkeypatch.setattr(mp, "_topcomp_cache", {}, raising=False)
    df = _df([{_CONF: "green", "منتج_المنافس": "x", "عدد_المنافسين": 1}])
    assert len(sort_most_rated(df)) == 1   # لا كسر
