"""tests/test_alert_service.py — تنبيه بأولوية: نفد عند المنافس ومتوفر عندك.

يثبّت:
- ``group_stockouts`` الخالصة: تجميع حسب المنتج + ترتيب (عدد المتاجر ثم الحداثة) + limit.
- ``filter_still_out`` الخالصة (ب2 التوسعة): يستبعد أزواج (منافس، منتج) عاد توفرها
  (back_in_stock أحدث من آخر stock_out لها) — «الاتجاه المعاكس».
- ``urgent_stockouts``: تقاطع stock_out مع our_catalog (نافذة الأيام تُحترم)،
  يستبعد ما أعاد توفره لاحقاً، وقاعدة غائبة/جدول غائب ⇒ [] بلا استثناء (اللوحة لا تنكسر).
"""
import sqlite3
from datetime import datetime, timedelta

import services.alert_service as als


def _ts(days_ago: int = 0, minutes: int = 0) -> str:
    return (datetime.now() - timedelta(days=days_ago, minutes=minutes)
            ).strftime("%Y-%m-%d %H:%M:%S")


def _mk_db(tmp_path, ours, events, moves=()):
    db = str(tmp_path / "a.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE our_catalog (norm_name TEXT)")
    conn.executemany("INSERT INTO our_catalog VALUES (?)", [(o,) for o in ours])
    conn.execute("""CREATE TABLE product_signal_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        competitor TEXT, norm_name TEXT, run_at TEXT, event TEXT,
        old_val REAL, new_val REAL)""")
    conn.executemany(
        "INSERT INTO product_signal_events (competitor, norm_name, run_at, event)"
        " VALUES (?,?,?,?)", events)
    if moves:  # صفوف تحرّك سعر كاملة (منافس, اسم, وقت, حدث, قديم, جديد)
        conn.executemany(
            "INSERT INTO product_signal_events"
            " (competitor, norm_name, run_at, event, old_val, new_val)"
            " VALUES (?,?,?,?,?,?)", moves)
    conn.commit(); conn.close()
    return db


# ── الطبقة النقيّة ───────────────────────────────────────────────────────────
def test_group_stockouts_ranking_and_limit():
    hits = [
        ("متجر1", "عطر أ", "2026-07-09 10:00:00"),
        ("متجر2", "عطر أ", "2026-07-08 09:00:00"),   # عطر أ: متجران
        ("متجر1", "عطر ب", "2026-07-09 12:00:00"),   # عطر ب: متجر واحد أحدث
        ("متجر3", "عطر ج", "2026-07-01 08:00:00"),
    ]
    out = als.group_stockouts(hits)
    assert [g["name"] for g in out] == ["عطر أ", "عطر ب", "عطر ج"]
    assert out[0]["n_stores"] == 2 and out[0]["stores"] == ["متجر1", "متجر2"]
    assert out[0]["last_at"] == "2026-07-09 10:00:00"
    assert als.group_stockouts(hits, limit=1) == out[:1]
    assert als.group_stockouts([]) == []


# ── الاتجاه المعاكس: كشف إعادة التوفر (ب2 التوسعة) ─────────────────────────
def test_filter_still_out_excludes_restocked_pair():
    events = [
        ("سولارا", "عطر أ", "stock_out", "2026-07-08 10:00:00"),
        ("سولارا", "عطر أ", "back_in_stock", "2026-07-09 10:00:00"),  # أحدث ⇒ عاد للتوفر
        ("نواعم", "عطر ب", "stock_out", "2026-07-09 09:00:00"),        # لا يزال نافداً
    ]
    out = als.filter_still_out(events)
    assert out == [("نواعم", "عطر ب", "2026-07-09 09:00:00")]


def test_filter_still_out_restock_before_stockout_still_counts():
    # إعادة توفر قديمة ثم نفاد جديد لاحق ⇒ لا يزال نافداً الآن (الأحدث يحكم).
    events = [
        ("سولارا", "عطر ج", "back_in_stock", "2026-07-01 10:00:00"),
        ("سولارا", "عطر ج", "stock_out", "2026-07-08 10:00:00"),
    ]
    out = als.filter_still_out(events)
    assert out == [("سولارا", "عطر ج", "2026-07-08 10:00:00")]


def test_filter_still_out_empty_input():
    assert als.filter_still_out([]) == []


# ── التقاطع مع الكتالوج ─────────────────────────────────────────────────────
def test_urgent_stockouts_intersects_and_respects_window(tmp_path):
    db = _mk_db(
        tmp_path,
        ours=["عطر نبيعه", "منتج آخر لنا"],
        events=[
            ("سولارا", "عطر نبيعه", _ts(1), "stock_out"),        # ✅ تقاطع
            ("نواعم",  "عطر نبيعه", _ts(2), "stock_out"),        # ✅ متجر ثانٍ
            ("سولارا", "عطر لا نبيعه", _ts(1), "stock_out"),     # ليس في كتالوجنا
            ("سولارا", "عطر نبيعه", _ts(30), "stock_out"),       # خارج النافذة
            ("سولارا", "منتج آخر لنا", _ts(1), "price_down"),    # ليس نفاداً
        ],
    )
    out = als.urgent_stockouts(days=7, db_path=db)
    assert len(out) == 1
    assert out[0]["name"] == "عطر نبيعه" and out[0]["n_stores"] == 2
    assert set(out[0]["stores"]) == {"سولارا", "نواعم"}


def test_urgent_stockouts_case_insensitive_match(tmp_path):
    db = _mk_db(tmp_path, ours=["Dior Sauvage 100ml "],
                events=[("متجر", "dior sauvage 100ml", _ts(0, 30), "stock_out")])
    out = als.urgent_stockouts(db_path=db)
    assert len(out) == 1   # strip().lower() يجسّر فرق الحالة/الفراغ


def test_urgent_stockouts_failopen(tmp_path):
    assert als.urgent_stockouts(db_path=str(tmp_path / "missing.db")) == []


def test_urgent_stockouts_excludes_restocked_product(tmp_path):
    db = _mk_db(
        tmp_path,
        ours=["عطر نبيعه", "عطر عاد للتوفر"],
        events=[
            ("سولارا", "عطر نبيعه", _ts(1), "stock_out"),          # ✅ لا يزال نافداً
            ("نواعم", "عطر عاد للتوفر", _ts(2), "stock_out"),
            ("نواعم", "عطر عاد للتوفر", _ts(1), "back_in_stock"),  # أحدث ⇒ عاد فاستُبعد
        ],
    )
    out = als.urgent_stockouts(days=7, db_path=db)
    assert [g["name"] for g in out] == ["عطر نبيعه"]


# ── الملخّص اليومي: الطبقة النقيّة ──────────────────────────────────────────
def test_rank_movers_biggest_move_per_product_sorted():
    hits = [
        ("متجر1", "عطر أ", 100.0, 90.0, "2026-07-09 10:00:00"),   # -10%
        ("متجر2", "عطر أ", 100.0, 60.0, "2026-07-09 11:00:00"),   # -40% ← الأكبر لعطر أ
        ("متجر1", "عطر ب", 50.0, 60.0, "2026-07-09 09:00:00"),    # +20%
    ]
    out = als.rank_movers(hits)
    assert [m["name"] for m in out] == ["عطر أ", "عطر ب"]  # ترتيب بـ|النسبة|
    assert out[0]["pct"] == -40.0 and out[0]["competitor"] == "متجر2"
    assert out[1]["pct"] == 20.0
    assert als.rank_movers(hits, limit=1) == out[:1]
    assert als.rank_movers([]) == []


def test_rank_movers_ignores_corrupt_prices():
    hits = [
        ("م", "عطر أ", 0, 50.0, "2026-07-09 10:00:00"),       # قديم=0 ⇒ يُهمل
        ("م", "عطر ب", None, 50.0, "2026-07-09 10:00:00"),    # قديم=None ⇒ يُهمل
        ("م", "عطر ج", 80.0, -5.0, "2026-07-09 10:00:00"),    # جديد سالب ⇒ يُهمل
        ("م", "عطر د", "نص", 50.0, "2026-07-09 10:00:00"),    # غير رقمي ⇒ يُهمل
    ]
    assert als.rank_movers(hits) == []


# ── الملخّص اليومي: القاعدة (تقاطع كتالوجنا + العدّادات + النافذة) ────────────
def test_daily_digest_movers_only_ours_counts_all(tmp_path):
    db = _mk_db(
        tmp_path,
        ours=["عطر نبيعه"],
        events=[("سولارا", "أي منتج", _ts(0, 30), "stock_out")],
        moves=[
            ("سولارا", "عطر نبيعه", _ts(0, 10), "price_down", 100.0, 80.0),  # ✅ لنا −20%
            ("نواعم", "عطر لا نبيعه", _ts(0, 10), "price_up", 50.0, 99.0),  # ليس لنا
            ("سولارا", "عطر نبيعه", _ts(0, 10) , "price_down", 0, 80.0),    # فاسد ⇒ يُهمل
        ],
    )
    d = als.daily_digest(hours=24, db_path=db)
    assert d["counts"]["stock_out"] == 1 and d["counts"]["price_down"] == 2
    assert d["counts"]["price_up"] == 1  # العدّادات عامة (كل المنافسين)
    assert [m["name"] for m in d["movers"]] == ["عطر نبيعه"]  # التحرّكات لنا فقط
    assert d["movers"][0]["pct"] == -20.0


def test_daily_digest_window_respected(tmp_path):
    db = _mk_db(
        tmp_path, ours=["عطر نبيعه"], events=[],
        moves=[("سولارا", "عطر نبيعه", _ts(3), "price_down", 100.0, 80.0)],  # قبل 3 أيام
    )
    d = als.daily_digest(hours=24, db_path=db)
    assert d == {"counts": {}, "movers": []}


def test_daily_digest_failopen(tmp_path):
    assert als.daily_digest(db_path=str(tmp_path / "missing.db")) == {}
