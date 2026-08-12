"""tests/test_signal_events.py — أساس إشارة الطلب: تسجيل تحوّلات التوفّر/السعر.

طبقة تسجيل بحتة فوق save_to_db: تُدرِج حدثاً في product_signal_events عند تحوّل
التوفّر (1↔0) أو تغيّر السعر ≥%2 فقط. المنتجات الثابتة لا تُنتِج أحداثاً، ولا يتغيّر
منطق الكشط/الحفظ/OOS.
"""
import sqlite3

from engines.mahally_scraper import MahallyScraper

_COMP = "متجر تجريبي"


def _events(db_path):
    con = sqlite3.connect(db_path)
    try:
        return con.execute(
            "SELECT event, old_val, new_val FROM product_signal_events ORDER BY id"
        ).fetchall()
    finally:
        con.close()


def _prod(name, price, available):
    return {"name": name, "price": price, "availability": available}


def test_stock_out_then_back_in_stock(tmp_path):
    db = str(tmp_path / "scrape.db")
    s = MahallyScraper(db_path=db)
    s.save_to_db([_prod("عطر تجريبي", 100.0, True)], _COMP)   # إدراج جديد ⇒ لا حدث
    assert _events(db) == []
    s.save_to_db([_prod("عطر تجريبي", 100.0, False)], _COMP)  # 1→0 ⇒ stock_out
    s.save_to_db([_prod("عطر تجريبي", 100.0, True)], _COMP)   # 0→1 ⇒ back_in_stock
    assert [e[0] for e in _events(db)] == ["stock_out", "back_in_stock"]


def test_stable_product_emits_no_events(tmp_path):
    db = str(tmp_path / "scrape.db")
    s = MahallyScraper(db_path=db)
    s.save_to_db([_prod("عطر ثابت", 100.0, True)], _COMP)
    s.save_to_db([_prod("عطر ثابت", 100.0, True)], _COMP)  # لا تغيير
    assert _events(db) == []


def test_price_up_then_down_over_threshold(tmp_path):
    db = str(tmp_path / "scrape.db")
    s = MahallyScraper(db_path=db)
    s.save_to_db([_prod("عطر", 100.0, True)], _COMP)
    s.save_to_db([_prod("عطر", 110.0, True)], _COMP)  # +10% ⇒ price_up
    s.save_to_db([_prod("عطر", 108.0, True)], _COMP)  # 110→108 = -1.8% < 2% ⇒ لا حدث
    s.save_to_db([_prod("عطر", 90.0, True)], _COMP)   # 108→90 = -16.7% ⇒ price_down
    evs = _events(db)
    assert [e[0] for e in evs] == ["price_up", "price_down"]
    assert evs[0][1] == 100.0 and evs[0][2] == 110.0  # old/new مسجّلان


def test_small_price_change_ignored(tmp_path):
    db = str(tmp_path / "scrape.db")
    s = MahallyScraper(db_path=db)
    s.save_to_db([_prod("عطر", 100.0, True)], _COMP)
    s.save_to_db([_prod("عطر", 101.0, True)], _COMP)  # +1% < 2% ⇒ لا حدث
    assert _events(db) == []


def test_stock_and_price_change_same_scrape_emit_both(tmp_path):
    db = str(tmp_path / "scrape.db")
    s = MahallyScraper(db_path=db)
    s.save_to_db([_prod("عطر", 100.0, True)], _COMP)
    # نفذ + السعر هبط ≥%2 في نفس الكشطة ⇒ حدثان
    s.save_to_db([_prod("عطر", 80.0, False)], _COMP)
    assert sorted(e[0] for e in _events(db)) == ["price_down", "stock_out"]
