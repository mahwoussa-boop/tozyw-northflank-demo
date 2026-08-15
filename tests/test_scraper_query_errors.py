# -*- coding: utf-8 -*-
"""حارس ضد تحويل فشل SQLite إلى رسالة «لا توجد بيانات» مضللة."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from services.scraper_service import ScraperService


_USER_SAFE_QUERY_ERROR = "تعذّر قراءة بيانات الكشط. أعد المحاولة لاحقاً."


def test_failed_query_is_nonfatal_but_exposed_for_the_ui(tmp_path, caplog):
    """الصفحة لا تنهار، لكن لا يجوز أن يبدو خطأ SQL كأنه نتيجة فارغة صحيحة."""
    db = tmp_path / "competitors.db"
    sqlite3.connect(db).close()
    service = ScraperService(links_file=tmp_path / "competitors.json", competitor_db=db)

    with caplog.at_level(logging.ERROR, logger="scraper_service"):
        assert service._query("SELECT * FROM table_that_does_not_exist") == []

    assert service.query_error == _USER_SAFE_QUERY_ERROR
    assert "تعذّر استعلام بيانات الكشط" in caplog.text


def test_successful_query_clears_previous_query_error(tmp_path):
    """لا يبقى تحذير قديم ظاهراً بعد نجاح قراءة لاحقة."""
    db = tmp_path / "competitors.db"
    sqlite3.connect(db).close()
    service = ScraperService(links_file=tmp_path / "competitors.json", competitor_db=db)

    service._query("SELECT * FROM table_that_does_not_exist")
    assert service.query_error == _USER_SAFE_QUERY_ERROR

    assert service._query("SELECT 1 AS ok") == [{"ok": 1}]
    assert service.query_error is None


def test_new_at_competitors_page_checks_query_error_before_empty_state():
    """فشل قراءة المتاجر يجب أن يعرض خطأً لا بطاقة «لا توجد بيانات كشط بعد» فقط."""
    root = Path(__file__).resolve().parents[1]
    source = (root / "ui" / "pages" / "new_at_competitors.py").read_text(encoding="utf-8")

    assert source.count("if scraper.query_error:") >= 3
    assert source.count("st.error(scraper.query_error)") >= 3
