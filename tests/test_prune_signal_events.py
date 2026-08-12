# -*- coding: utf-8 -*-
"""تدوير product_signal_events (اكتُشف 2026-07-22: append-only بلا حدّ أقصى).

يتحقّق أن prune_signal_events تحذف الصفوف الأقدم من مدة الاحتفاظ فقط، وتُبقي
الحديث والحدّي سليماً، بلا لمس أي جدول آخر.
"""
import sqlite3

from scripts.prune_signal_events import prune_signal_events


def _make_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE product_signal_events (id INTEGER PRIMARY KEY, competitor TEXT, "
        "norm_name TEXT, run_at TEXT, event TEXT, old_val REAL, new_val REAL)"
    )
    conn.execute("CREATE TABLE other_table (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO other_table (v) VALUES ('لا يُمَس')")
    rows = [
        ("c1", "n1", "old", "stock_out"),       # أقدم من 90 يوماً بكثير
        ("c1", "n2", "recent", "price_up"),      # حديث
    ]
    conn.executemany(
        "INSERT INTO product_signal_events (competitor, norm_name, run_at, event) "
        "VALUES (?, ?, datetime('now', ?), ?)",
        [
            ("c1", "n1", "-120 days", "stock_out"),
            ("c1", "n2", "-1 days", "price_up"),
            # -91 لا -90 بالضبط: تفادياً لتذبذب مقارنة على حدّ ثانية واحدة
            # بين وقت الإدراج ووقت التدوير (SQLite datetime() بدقّة الثانية).
            ("c1", "n3", "-91 days", "back_in_stock"),
        ],
    )
    conn.commit()
    conn.close()


def test_prune_deletes_only_rows_older_than_retention(tmp_path):
    db = str(tmp_path / "signals.db")
    _make_db(db)
    deleted = prune_signal_events(db, days=90)
    assert deleted == 2  # -120 و -91 (أقدم من 90 يوماً) يُحذفان، -1 يبقى

    conn = sqlite3.connect(db)
    remaining = conn.execute(
        "SELECT norm_name FROM product_signal_events"
    ).fetchall()
    other = conn.execute("SELECT v FROM other_table").fetchall()
    conn.close()
    assert remaining == [("n2",)]
    assert other == [("لا يُمَس",)]  # جدول آخر لم يُمَس إطلاقاً


def test_prune_empty_table_returns_zero(tmp_path):
    db = str(tmp_path / "empty.db")
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE product_signal_events (id INTEGER PRIMARY KEY, competitor TEXT, "
        "norm_name TEXT, run_at TEXT, event TEXT, old_val REAL, new_val REAL)"
    )
    conn.commit()
    conn.close()
    assert prune_signal_events(db, days=90) == 0
