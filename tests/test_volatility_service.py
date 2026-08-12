"""tests/test_volatility_service.py — حرارة المنافسين (جدولة الكشط المتنفّسة).

يثبّت:
- ``rank_heat`` الخالصة: التطبيع النسبي + الطبقات (حار/دافئ/راكد) + الترتيب التنازلي.
- ``order_by_heat`` الخالصة: الأسخن أولاً، والمجهول (بلا درجة) يتقدّم الجميع، فارغ ⇒ كما هو.
- ``rebuild_competitor_heat``: بناء ذرّي من أحداث النافذة فقط (القديم مستبعَد)،
  DELETE+INSERT بلا تراكم، وقاعدة غائبة ⇒ 0 بلا استثناء.
- ``heat_order``: قراءة الدرجات، وجدول غائب ⇒ {} (fail-open).
"""
import sqlite3
from datetime import datetime, timedelta

import services.volatility_service as vs


def _ts(days_ago: int = 0, minutes: int = 0) -> str:
    """طابع زمني نسبي — يُبقي الاختبار صالحاً في أي تاريخ تشغيل."""
    return (datetime.now() - timedelta(days=days_ago, minutes=minutes)
            ).strftime("%Y-%m-%d %H:%M:%S")


def _mk_events_db(tmp_path, rows):
    """قاعدة مصغّرة بجدول product_signal_events (competitor, norm_name, run_at, event)."""
    db = str(tmp_path / "v.db")
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE product_signal_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        competitor TEXT, norm_name TEXT, run_at TEXT, event TEXT,
        old_val REAL, new_val REAL)""")
    conn.executemany(
        "INSERT INTO product_signal_events (competitor, norm_name, run_at, event)"
        " VALUES (?,?,?,?)", rows)
    conn.commit(); conn.close()
    return db


# ── الطبقة النقيّة ───────────────────────────────────────────────────────────
def test_rank_heat_normalization_and_tiers():
    ranked = vs.rank_heat([
        {"competitor": "حار جداً", "n_events": 100, "n_products": 50},
        {"competitor": "دافئ",     "n_events": 30,  "n_products": 10},
        {"competitor": "راكد",     "n_events": 2,   "n_products": 1},
    ])
    assert [r["competitor"] for r in ranked] == ["حار جداً", "دافئ", "راكد"]
    assert ranked[0]["score"] == 1.0 and ranked[0]["tier"] == "حار"
    assert ranked[1]["tier"] == "دافئ"
    assert ranked[2]["tier"] == "راكد"


def test_rank_heat_empty():
    assert vs.rank_heat([]) == []


def test_order_by_heat_hot_first_unknown_leads():
    comps = ["أ", "ب", "ج", "جديد"]
    heat = {"أ": 0.2, "ب": 0.9, "ج": 0.05}
    assert vs.order_by_heat(comps, heat, name_of=str) == ["جديد", "ب", "أ", "ج"]
    # heat فارغ ⇒ الترتيب الأصلي كما هو (fail-open)
    assert vs.order_by_heat(comps, {}, name_of=str) == comps


# ── البناء والقراءة ─────────────────────────────────────────────────────────
def test_rebuild_counts_window_only_and_idempotent(tmp_path):
    db = _mk_events_db(tmp_path, [
        ("سولارا", "عطر1", _ts(1), "price_down"),
        ("سولارا", "عطر2", _ts(0, 30), "price_up"),
        ("سولارا", "عطر1", _ts(0, 10), "stock_out"),
        ("راكد",   "عطر3", _ts(0, 60), "price_up"),
        ("قديم",   "عطر4", _ts(60), "price_down"),   # خارج نافذة HOT_DAYS
        ("سولارا", "عطر5", _ts(0, 90), "rating_up"),  # حدث غير سعري — مستبعَد
    ])
    n = vs.rebuild_competitor_heat(db)
    assert n == 2   # «قديم» خارج النافذة و«rating_up» لا يُحتسب
    rows = sqlite3.connect(db).execute(
        "SELECT competitor, n_events, n_products, tier FROM competitor_heat"
        " ORDER BY score DESC").fetchall()
    assert rows[0][:3] == ("سولارا", 3, 2) and rows[0][3] == "حار"
    assert rows[1][0] == "راكد"
    # إعادة البناء لا تراكم صفوفاً (DELETE+INSERT ذرّي)
    assert vs.rebuild_competitor_heat(db) == 2
    total = sqlite3.connect(db).execute(
        "SELECT COUNT(*) FROM competitor_heat").fetchone()[0]
    assert total == 2


def test_rebuild_missing_db_returns_zero(tmp_path):
    assert vs.rebuild_competitor_heat(str(tmp_path / "missing.db")) == 0


def test_heat_order_reads_scores_and_failopen(tmp_path):
    db = _mk_events_db(tmp_path, [
        ("أ", "ع1", _ts(0, 30), "price_up"),
        ("ب", "ع2", _ts(0, 30), "price_down"),
        ("ب", "ع3", _ts(0, 25), "price_down"),
    ])
    vs.rebuild_competitor_heat(db)
    heat = vs.heat_order(db)
    assert set(heat) == {"أ", "ب"} and heat["ب"] > heat["أ"]
    # قاعدة بلا جدول حرارة ⇒ {} — الكشط يمضي بترتيبه الأصلي (fail-open)
    assert vs.heat_order(str(tmp_path / "no_heat_table.db")) == {}
