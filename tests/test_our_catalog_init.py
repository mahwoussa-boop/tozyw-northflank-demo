"""tests/test_our_catalog_init.py — حارس انحدار لخطأ «no such table: our_catalog».

السبب الجذري: جداول v26 (our_catalog / comp_catalog / processed_products) تُنشأ
حصراً داخل ``init_db_v26()``، وهي لم تكن مستدعاة في تسلسل تحميل الوحدة (كان يشغّل
``init_db()`` + ``init_db_v35()`` فقط). فكانت ``ensure_indexes()`` تتخطّى الجداول
الغائبة بصمت، ينجح الإقلاع، ثم ينهار التطبيق لاحقاً عند ``SELECT … FROM our_catalog``.

الإصلاح: استدعاء ``init_db_v26()`` في تسلسل التحميل **قبل** ``init_db()`` كي توجد
الجداول حين تفهرسها ``ensure_indexes()`` (idx_ourcat_norm).
"""
import inspect
import re
import sqlite3

import utils.db_manager as dbm


def _schema(db_path: str) -> tuple[set[str], set[str]]:
    """(جداول، فهارس) قاعدة بيانات SQLite عبر اتصال مستقل (لا يلمس get_db)."""
    con = sqlite3.connect(db_path)
    try:
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        indexes = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
    finally:
        con.close()
    return tables, indexes


def _run_module_init() -> None:
    """يكرّر تسلسل تهيئة تحميل الوحدة بالترتيب المُصلَح (v26 أولاً ثم الفهرسة)."""
    dbm.init_db_v26()
    dbm.init_db()
    dbm.init_db_v35()


def test_fresh_db_gets_v26_tables_and_ourcat_index(tmp_path, monkeypatch):
    """DB طازجة بعد التهيئة تحوي الجداول الثلاثة + فهرس our_catalog."""
    fresh = str(tmp_path / "fresh.db")
    monkeypatch.setattr(dbm, "DB_PATH", fresh)

    _run_module_init()

    tables, indexes = _schema(fresh)
    assert "our_catalog" in tables
    assert "comp_catalog" in tables
    assert "processed_products" in tables
    # init_db()->ensure_indexes() لا يفهرس our_catalog إلا إذا أُنشئ الجدول قبله
    assert "idx_ourcat_norm" in indexes


def test_get_our_catalog_works_on_fresh_db(tmp_path, monkeypatch):
    """الاستدعاء الإنتاجي الذي انهار (get_our_catalog) يعمل على DB طازجة بلا خطأ."""
    fresh = str(tmp_path / "fresh2.db")
    monkeypatch.setattr(dbm, "DB_PATH", fresh)

    _run_module_init()

    # قبل الإصلاح: sqlite3.OperationalError: no such table: our_catalog
    assert dbm.get_our_catalog() == []


def test_module_init_wires_init_db_v26_before_init_db():
    """حارس الوصل: init_db_v26() مستدعاة في تسلسل التحميل قبل init_db()."""
    src = inspect.getsource(dbm)
    assert re.search(
        r"init_db_v26\(\)\s+init_db\(\)\s+init_db_v35\(\)", src
    ), "init_db_v26() يجب أن يسبق init_db() في تسلسل تحميل الوحدة"
