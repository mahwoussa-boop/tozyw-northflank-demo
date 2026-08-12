"""
tests/test_decision_log.py — اختبار سجلّ القرارات الإلحاقيّ
============================================================
يختبر ``append_price_decision`` على **قاعدة مؤقتة في tmp** (لا القاعدة الحقيقية):
يُحقَن ``get_db`` عبر monkeypatch ليشير إلى ملفٍّ مؤقّت. الجوهر: قراران لنفس
المنتج نفس اليوم ⇒ صفّان (إلحاقيّ لا استبدال) — عكس ``upsert_price_history``.
"""
import sqlite3

import pytest

import utils.db_manager as dbm

# نفس مخطّط price_history الحيّ (db_manager.py) — نسخة مؤقتة للاختبار فقط.
_SCHEMA = """CREATE TABLE price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT, product_name TEXT, competitor TEXT, price REAL,
    our_price REAL, diff REAL, match_score REAL, decision TEXT,
    product_id TEXT DEFAULT ''
)"""


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """قاعدة SQLite مؤقتة + حقن get_db ليكتب فيها بدل القاعدة الحقيقية."""
    dbfile = str(tmp_path / "test_pricing.db")
    conn = sqlite3.connect(dbfile)
    conn.execute(_SCHEMA)
    conn.commit()
    conn.close()
    monkeypatch.setattr(dbm, "get_db", lambda: sqlite3.connect(dbfile))
    return dbfile


def _rows(dbfile):
    conn = sqlite3.connect(dbfile)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM price_history ORDER BY id")]
    finally:
        conn.close()


def test_two_decisions_same_day_append_not_replace(temp_db):
    """جوهر الفرق: قراران لنفس المنتج نفس اليوم ⇒ صفّان مستقلان، لا استبدال."""
    dbm.append_price_decision("عطر تجريبي", our_price=199, price=210,
                              decision="approved")
    dbm.append_price_decision("عطر تجريبي", our_price=205, price=210,
                              decision="edited")
    rows = _rows(temp_db)
    assert len(rows) == 2                                   # لا استبدال
    assert [r["decision"] for r in rows] == ["approved", "edited"]
    # نفس المنتج في الصفّين (لم يُدمَج/يُستبدَل)
    assert {r["product_name"] for r in rows} == {"عطر تجريبي"}


def test_values_persisted_as_passed(temp_db):
    """القيم تُحفظ كما مُرِّرت، و diff = our_price - price."""
    dbm.append_price_decision("منتج", our_price=199, price=210,
                              decision="approved", competitor="market",
                              match_score=88, product_id="P1")
    r = _rows(temp_db)[0]
    assert r["our_price"] == 199
    assert r["price"] == 210
    assert r["decision"] == "approved"
    assert r["diff"] == 199 - 210           # -11
    assert r["competitor"] == "market"
    assert r["product_id"] == "P1"


def test_both_decision_kinds_written(temp_db):
    """approved و edited كلاهما يُكتبان."""
    dbm.append_price_decision("A", our_price=100, price=120, decision="approved")
    dbm.append_price_decision("A", our_price=110, price=120, decision="edited")
    kinds = {r["decision"] for r in _rows(temp_db)}
    assert kinds == {"approved", "edited"}


def test_diff_positive_when_above_reference(temp_db):
    """diff موجب حين يتجاوز السعر المعتمَد مرجع السوق."""
    dbm.append_price_decision("B", our_price=150, price=100, decision="edited")
    assert _rows(temp_db)[0]["diff"] == 50


def test_no_write_to_real_db_isolation(temp_db):
    """كل الكتابة في الملف المؤقّت فقط (get_db المحقون)."""
    dbm.append_price_decision("iso", our_price=10, price=9, decision="approved")
    assert len(_rows(temp_db)) == 1
