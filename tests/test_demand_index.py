"""م7 — مؤشر الطلب المقدَّر: يبني من مجرى الأحداث، ولا يدّعي مبيعات.

533,361 حدثاً على 33 يوماً متميّزاً عبر 77 متجراً تجعل المكوّنات الخمسة متاحة
كلها. القيود المُختبَرة هنا ليست تجميلاً: المؤشر تقديريّ، نسبيّ، مصحوب بمصدره
ونافذته، ولا يُحوَّل إلى وحدات مباعة أو إيراد.
"""
from __future__ import annotations

import sqlite3

import pytest

from services import demand_index as di


def _row(**kw):
    base = {"norm_name": "عطر", "store_count": 1, "rating_up": 0, "stock_out": 0,
            "back_in_stock": 0, "price_up": 0, "price_down": 0}
    base.update(kw)
    return base


# ── الطبقة النقيّة ──────────────────────────────────────────────────────────

def test_rating_up_weighs_more_than_price_up():
    """التقييم أقرب دليل إلى شراء فعلي ⇒ وزنه أعلى."""
    assert di.raw_demand_score(_row(rating_up=1)) > di.raw_demand_score(_row(price_up=1))


def test_price_down_is_a_negative_signal():
    assert di.raw_demand_score(_row(price_down=3)) < 0


def test_score_is_relative_and_capped_at_100():
    rows = di.score_rows([
        _row(norm_name="أ", rating_up=10),
        _row(norm_name="ب", rating_up=5),
        _row(norm_name="ج", price_down=8),
    ])
    by = {r["norm_name"]: r for r in rows}
    assert by["أ"]["demand_score"] == 100.0
    assert by["ب"]["demand_score"] == 50.0
    # درجة سالبة تُقصّ إلى صفر — «طلب منخفض» لا طلب سالب
    assert by["ج"]["demand_score"] == 0.0
    assert by["ج"]["demand_tier"] == "منخفض"


def test_rows_come_back_sorted_by_score():
    rows = di.score_rows([_row(norm_name="أ", rating_up=1),
                          _row(norm_name="ب", rating_up=9)])
    assert [r["norm_name"] for r in rows] == ["ب", "أ"]


def test_empty_input_does_not_divide_by_zero():
    assert di.score_rows([]) == []
    assert di.score_rows([_row()])[0]["demand_score"] == 0.0


def test_tier_boundaries():
    assert di.tier_of(100.0) == "مرتفع"
    assert di.tier_of(di.TIER_HIGH) == "مرتفع"
    assert di.tier_of(di.TIER_MEDIUM) == "متوسط"
    assert di.tier_of(0.0) == "منخفض"


def test_explain_gives_every_component_a_source_and_a_window():
    """شرط م7: لا مكوّن بلا مصدر ونافذة — رقمٌ مجرّد يُقرأ كأنه مبيعات."""
    parts = di.explain(_row(rating_up=2, stock_out=1))
    assert len(parts) == len(di.COMPONENTS)
    for part in parts:
        assert part["المصدر"].strip()
        assert str(di.WINDOW_DAYS) in part["النافذة"]
    assert {p["المكوّن"] for p in parts} >= {"تقييمات جديدة", "نفاد مخزون"}


def test_index_never_speaks_in_sales_units():
    """حارس لغة: ممنوع «مباع/إيراد/حصة سوقية» في أي مخرَج يراه المالك."""
    forbidden = ("مباع", "إيراد", "حصة سوقية", "مبيعات")
    surfaces = [p["المكوّن"] for p in di.explain(_row())]
    surfaces += [p["المصدر"] for p in di.explain(_row())]
    surfaces += [di.tier_of(v) for v in (0.0, 20.0, 80.0)]
    for text in surfaces:
        assert not any(word in text for word in forbidden), text


def test_ambiguous_first_seen_out_is_not_a_component():
    """«ظهر نافداً» يخلط المطلوب بغير المورَّد ⇒ مستبعَد عمداً."""
    assert "first_seen_out" not in {key for key, *_ in di.COMPONENTS}
    assert "first_seen_out" not in di._AGG_SQL


# ── طبقة القاعدة ────────────────────────────────────────────────────────────

def _events_db(tmp_path, rows):
    db = tmp_path / "pricing_v18.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE product_signal_events (id INTEGER PRIMARY KEY, competitor TEXT,"
        " norm_name TEXT, run_at TEXT, event TEXT, old_val REAL, new_val REAL)"
    )
    con.executemany(
        "INSERT INTO product_signal_events (competitor, norm_name, run_at, event)"
        " VALUES (?,?,datetime('now','localtime',?),?)", rows,
    )
    con.commit()
    con.close()
    return str(db)


def test_compute_reads_events_and_counts_stores(tmp_path):
    db = _events_db(tmp_path, [
        ("متجر أ", "عطر مطلوب", "-1 days", "rating_up"),
        ("متجر ب", "عطر مطلوب", "-2 days", "stock_out"),
        ("متجر أ", "عطر راكد", "-1 days", "price_down"),
    ])
    rows = di.compute_demand_index(db)
    top = rows[0]
    assert top["norm_name"] == "عطر مطلوب"
    assert top["store_count"] == 2
    assert top["demand_score"] == 100.0


def test_events_outside_the_window_are_ignored(tmp_path):
    db = _events_db(tmp_path, [
        ("متجر أ", "قديم", f"-{di.WINDOW_DAYS + 10} days", "rating_up"),
        ("متجر أ", "حديث", "-1 days", "rating_up"),
    ])
    names = {r["norm_name"] for r in di.compute_demand_index(db)}
    assert names == {"حديث"}


def test_rebuild_is_atomic_and_readable(tmp_path):
    db = _events_db(tmp_path, [("متجر أ", "عطر", "-1 days", "rating_up")])
    assert di.rebuild_demand_index(db) == 1
    got = di.demand_for(["عطر"], db_path=db)
    assert got["عطر"]["demand_tier"] == "مرتفع"
    assert got["عطر"]["window_days"] == di.WINDOW_DAYS
    # إعادة البناء مرّتين لا تُراكم صفوفاً
    assert di.rebuild_demand_index(db) == 1
    assert len(di.demand_for(["عطر"], db_path=db)) == 1


def test_missing_db_is_fail_open_everywhere():
    """غياب القاعدة لا يكسر صفحة ولا يرفع استثناء."""
    assert di.compute_demand_index("/no/such.db") == []
    assert di.rebuild_demand_index("/no/such.db") == 0
    assert di.demand_for(["أي"], db_path="/no/such.db") == {}
    assert di.demand_for([], db_path="/no/such.db") == {}
