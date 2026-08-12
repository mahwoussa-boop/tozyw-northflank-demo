# -*- coding: utf-8 -*-
"""إغلاق فجوة أحداث النفاد: التعليم-بالغياب يسجّل stock_out فردياً.

كان التحديث الجماعي (availability=0 لمنتج غائب عن كشطة مكتملة) صامتاً بلا
أحداث؛ الآن يُدرَج stock_out لكل صف مُلتقَط (1.0 → 0.0).
"""
import sqlite3

from engines.mahally_scraper import MahallyScraper


def _prod(name, avail=True):
    return {"name": name, "price": 100.0, "availability": avail, "image": "", "url": "",
            "brand": "", "rating_count": 0, "discount_pct": 0, "original_price": 0,
            "category": "", "sku": "", "image_urls": ""}


def _age_all(db):
    """يدفع updated_at للماضي (يحاكي فارق الزمن بين كشطتين حقيقيتين)."""
    con = sqlite3.connect(db)
    con.execute("UPDATE competitor_products_store SET updated_at='2020-01-01 00:00:00'")
    con.commit()
    con.close()


def test_absence_marks_oos_and_logs_stock_out(tmp_path):
    """منتج غائب عن كشطة تتجاوز العتبة ⇒ availability=0 + حدث stock_out (1→0)."""
    db = str(tmp_path / "a.db")
    s = MahallyScraper(db_path=db)
    s.save_to_db([_prod("منتج ألف"), _prod("منتج باء")], "متجر")   # prior=2
    _age_all(db)
    s.save_to_db([_prod("منتج ألف")], "متجر")                       # saved=1 ≥ 2*0.5 ⇒ P2 يغيب
    con = sqlite3.connect(db)
    av = con.execute(
        "SELECT availability FROM competitor_products_store WHERE product_name='منتج باء'"
    ).fetchone()[0]
    ev = con.execute(
        "SELECT event, old_val, new_val FROM product_signal_events WHERE event='stock_out'"
    ).fetchall()
    con.close()
    assert av == 0
    assert ev == [("stock_out", 1.0, 0.0)]


def test_below_threshold_no_flip_no_event(tmp_path):
    """كشطة دون العتبة (saved < prior*0.5) ⇒ لا قلب ولا أحداث."""
    db = str(tmp_path / "b.db")
    s = MahallyScraper(db_path=db)
    s.save_to_db([_prod(f"م{i}") for i in range(10)], "متجر")       # prior=10
    _age_all(db)
    s.save_to_db([_prod("م0")], "متجر")                             # saved=1 < 5
    con = sqlite3.connect(db)
    n0 = con.execute(
        "SELECT COUNT(*) FROM competitor_products_store WHERE availability=0"
    ).fetchone()[0]
    nev = con.execute(
        "SELECT COUNT(*) FROM product_signal_events WHERE event='stock_out'"
    ).fetchone()[0]
    con.close()
    assert n0 == 0 and nev == 0
