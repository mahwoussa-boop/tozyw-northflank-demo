"""tests/test_decision_journal.py — حارس دفتر القرارات الصامت (كتابة فقط في events).

يختبر ``record_decision`` على قاعدة مؤقتة (لا القاعدة الحقيقية): إدراج صفّ صحيح
بالحقول والـJSON، دمج مفاتيح extra، رفض المفردات المجهولة، وابتلاع فشل القاعدة.
"""
import json
import sqlite3

import pytest

import utils.db_manager as dbm
from services.decision_journal import record_decision

# نفس مخطّط events الحيّ — نسخة مؤقتة للاختبار فقط.
_SCHEMA = """CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT, page TEXT, event_type TEXT, details TEXT,
    product_name TEXT, action_taken TEXT
)"""


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """قاعدة SQLite مؤقتة + حقن get_db ليكتب فيها بدل القاعدة الحقيقية."""
    dbfile = str(tmp_path / "test_events.db")
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
        return [dict(r) for r in conn.execute("SELECT * FROM events ORDER BY id")]
    finally:
        conn.close()


def test_valid_call_inserts_one_row(temp_db):
    """(أ) استدعاء صحيح ⇒ صفّ واحد بالحقول والـJSON الصحيحين."""
    record_decision("send_price", "عطر تجريبي", "price_raise",
                    suggested=210.0, chosen=205.0)
    rows = _rows(temp_db)
    assert len(rows) == 1
    r = rows[0]
    assert r["event_type"] == "send_price"
    assert r["product_name"] == "عطر تجريبي"
    assert r["page"] == "price_raise"
    assert r["action_taken"] == "إرسال تحديث سعر"
    d = json.loads(r["details"])
    assert d == {"suggested": 210.0, "chosen": 205.0, "actor": "shared"}


def test_extra_keys_merged_into_details(temp_db):
    """extra يُدمج في details دون كسر المفاتيح القياسية."""
    record_decision("send_new", "منتج", "missing", chosen=99.0,
                    extra={"batch": True})
    d = json.loads(_rows(temp_db)[0]["details"])
    assert d["batch"] is True
    assert d["chosen"] == 99.0
    assert d["actor"] == "shared"


def test_unknown_event_type_inserts_nothing(temp_db):
    """(ج) نوع حدث خارج المفردات ⇒ لا صفّ يُدرج."""
    record_decision("bogus", "منتج", "missing", chosen=10.0)
    assert _rows(temp_db) == []


def test_journal_failure_does_not_raise(monkeypatch):
    """(ب) كسر متعمّد ⇒ لا استثناء يخرج من الدالة (القاعدة 5)."""
    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(dbm, "log_event", _boom)
    record_decision("ignore", "منتج", "price_lower")  # يجب ألا يرفع
