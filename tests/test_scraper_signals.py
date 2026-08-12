# -*- coding: utf-8 -*-
"""إصلاح التقاط إشارات الكاشط (خطوة ب+د).

commit 1: _parse_hit يقرأ التقييم من all_rating (لا rating_count المفقود).
commit 2: منتج جديد يُرصد نافداً يسجّل حدث first_seen_out (فرع الإدراج).
"""
import sqlite3

from engines.mahally_scraper import MahallyScraper, _log_signal_events

_S = MahallyScraper(db_path=None)


def _save_one_new(tmp_path, availability):
    """يحفظ منتجاً جديداً واحداً في قاعدة مؤقتة ويعيد عدد أحداث first_seen_out."""
    db = str(tmp_path / "signals.db")
    scraper = MahallyScraper(db_path=db)
    product = {
        "name": "منتج اختبار جديد", "price": 100.0, "availability": availability,
        "image": "", "url": "", "brand": "", "rating_count": 0,
        "discount_pct": 0, "original_price": 0, "category": "", "sku": "",
    }
    scraper.save_to_db([product], "متجر اختبار")
    con = sqlite3.connect(db)
    n = con.execute(
        "SELECT COUNT(*) FROM product_signal_events WHERE event='first_seen_out'"
    ).fetchone()[0]
    con.close()
    return n


def test_parse_hit_reads_rating_from_all_rating():
    """all_rating={count:7,average:4.3} ⇒ rating_count==7 و rating_avg==4.3."""
    hit = {"name": "عطر اختبار", "price": 100,
           "all_rating": {"count": 7, "average": 4.3}}
    row = _S._parse_hit(hit, "متجر اختبار")
    assert row["rating_count"] == 7
    assert row["rating_avg"] == 4.3


def test_parse_hit_missing_all_rating_is_zero_no_error():
    """hit بلا all_rating ⇒ rating_count==0 بلا استثناء، و rating_avg=None.

    (حُدِّث في خطوة multi-images: rating_avg صار None عند غياب التقييم
    لتمييز «بلا تقييم» عن «تقييمه صفر» — كان يؤكّد 0.0 قبلها.)
    """
    row = _S._parse_hit({"name": "عطر اختبار", "price": 100}, "متجر اختبار")
    assert row["rating_count"] == 0
    assert row["rating_avg"] is None


def test_new_out_of_stock_logs_first_seen_out(tmp_path):
    """منتج جديد نافد (avail=0) ⇒ حدث first_seen_out واحد."""
    assert _save_one_new(tmp_path, False) == 1


def test_new_in_stock_logs_no_event(tmp_path):
    """منتج جديد متوفر (avail=1) ⇒ لا حدث first_seen_out."""
    assert _save_one_new(tmp_path, True) == 0


def _rating_events(old_r, new_r):
    """يشغّل _log_signal_events بلا تغيّر توفّر/سعر ويعيد كل الأحداث المُدرَجة."""
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE product_signal_events (id INTEGER PRIMARY KEY, competitor TEXT, "
        "norm_name TEXT, run_at TEXT, event TEXT, old_val REAL, new_val REAL)"
    )
    cur = con.cursor()
    _log_signal_events(cur, "c", "n", "t", old_avail=1, new_avail=1,
                       old_price=100, new_price=100, old_rating=old_r, new_rating=new_r)
    rows = cur.execute("SELECT event, old_val, new_val FROM product_signal_events").fetchall()
    con.close()
    return rows


def test_rating_up_on_increase():
    """زيادة التقييم 1→3 ⇒ حدث rating_up واحد بقيمتيه old=1, new=3."""
    assert _rating_events(1, 3) == [("rating_up", 1.0, 3.0)]


def test_no_rating_event_on_stable_or_decrease():
    """ثبات 2→2 ونقصان 3→1 ⇒ صفر أحداث."""
    assert _rating_events(2, 2) == []
    assert _rating_events(3, 1) == []
