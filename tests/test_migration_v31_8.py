# -*- coding: utf-8 -*-
"""هجرة v31.8 + rating_avg: init_competitor_store يضيف أعمدة الاستخراج المسبق.

يُعزل بـmonkeypatch على get_db كي لا تُلمَس القاعدة الحيّة.
"""
import sqlite3

from utils import db_manager

_V31_8_COLS = (
    "extracted_brand", "extracted_size", "extracted_type", "extracted_gender",
    "extracted_class", "agg_name", "product_line", "image_urls", "rating_avg",
)


def test_init_competitor_store_adds_nine_cols_idempotent(tmp_path, monkeypatch):
    """استدعاء init_competitor_store (مرّتين) ⇒ الأعمدة التسعة تظهر بلا خطأ."""
    db = str(tmp_path / "mig.db")
    monkeypatch.setattr(db_manager, "get_db", lambda: sqlite3.connect(db))
    db_manager.init_competitor_store()
    db_manager.init_competitor_store()  # idempotent — لا يرمي استثناءً
    con = sqlite3.connect(db)
    cols = {r[1] for r in con.execute("PRAGMA table_info(competitor_products_store)")}
    con.close()
    for c in _V31_8_COLS:
        assert c in cols, f"العمود {c} مفقود"
