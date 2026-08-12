"""اختبار هجرة فهرس عدّاد/عرض «نفذت» (idx_cps_avail_updated) — الخطوة ٤.

يُعزل بـ monkeypatch على ``get_db`` كي لا تُلمَس القاعدة الحيّة (نمط v31.8 نفسه).
يتحقّق أن مسار init ينشئ الفهرس المركّب ويسجّل الهجرة — وأنه idempotent.
"""
import sqlite3

from utils import db_manager


def test_ensure_indexes_creates_oos_index_and_registers_version(tmp_path, monkeypatch):
    db = str(tmp_path / "mig.db")
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE competitor_products_store ("
        "id INTEGER PRIMARY KEY, competitor TEXT, availability INTEGER, updated_at TEXT)"
    )
    con.execute(
        "CREATE TABLE db_version (version TEXT PRIMARY KEY, "
        "applied_at TEXT DEFAULT (datetime('now','localtime')), description TEXT)"
    )
    con.commit()
    con.close()

    monkeypatch.setattr(db_manager, "get_db", lambda: sqlite3.connect(db))
    db_manager.ensure_indexes()
    db_manager.ensure_indexes()  # idempotent — لا يرمي استثناءً ولا يكرّر

    con = sqlite3.connect(db)
    indexes = {
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='competitor_products_store'"
        )
    }
    idx_sql = con.execute(
        "SELECT sql FROM sqlite_master WHERE name='idx_cps_avail_updated'"
    ).fetchone()[0]
    versions = {r[0] for r in con.execute("SELECT version FROM db_version")}
    con.close()

    assert "idx_cps_avail_updated" in indexes
    assert "availability" in idx_sql and "updated_at" in idx_sql
    assert "v31.9" in versions


def test_ensure_indexes_creates_data_age_indexes_and_registers_version(tmp_path, monkeypatch):
    """هـ1 (2026-07-22): idx_cps_url_updated + idx_cps_updated لتسريع شارة قِدَم
    البيانات (_data_ages/latest_data_age_days) — نفس نمط idx_cps_avail_updated."""
    db = str(tmp_path / "mig2.db")
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE competitor_products_store ("
        "id INTEGER PRIMARY KEY, product_url TEXT, updated_at TEXT)"
    )
    con.execute(
        "CREATE TABLE db_version (version TEXT PRIMARY KEY, "
        "applied_at TEXT DEFAULT (datetime('now','localtime')), description TEXT)"
    )
    con.commit()
    con.close()

    monkeypatch.setattr(db_manager, "get_db", lambda: sqlite3.connect(db))
    db_manager.ensure_indexes()
    db_manager.ensure_indexes()  # idempotent

    con = sqlite3.connect(db)
    indexes = {
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='competitor_products_store'"
        )
    }
    versions = {r[0] for r in con.execute("SELECT version FROM db_version")}
    con.close()

    assert "idx_cps_url_updated" in indexes
    assert "idx_cps_updated" in indexes
    assert "v31.10" in versions


def test_ensure_indexes_safe_without_db_version_table(tmp_path, monkeypatch):
    """غياب جدول db_version لا يكسر إنشاء الفهرس (تسجيل الهجرة اختياري وآمن)."""
    db = str(tmp_path / "nover.db")
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE competitor_products_store ("
        "id INTEGER PRIMARY KEY, competitor TEXT, availability INTEGER, updated_at TEXT)"
    )
    con.commit()
    con.close()

    monkeypatch.setattr(db_manager, "get_db", lambda: sqlite3.connect(db))
    db_manager.ensure_indexes()  # لا يرمي رغم غياب db_version

    con = sqlite3.connect(db)
    indexes = {
        r[0]
        for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='competitor_products_store'"
        )
    }
    con.close()
    assert "idx_cps_avail_updated" in indexes
