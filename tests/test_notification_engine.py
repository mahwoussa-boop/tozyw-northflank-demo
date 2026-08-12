"""tests/test_notification_engine.py — اختبارات محرك التنبيهات."""
import tempfile, os, time
import pytest
from datetime import datetime, timedelta, timezone

from infrastructure.db_manager import DatabaseManager
from services.workflow.notification_engine import NotificationEngine, Notification


@pytest.fixture
def engine():
    tmp = tempfile.mktemp(suffix=".db")
    db = DatabaseManager(tmp)
    eng = NotificationEngine(db)
    eng.ensure_table()
    yield eng
    try:
        os.unlink(tmp)
    except OSError:
        pass


class TestEnsureTable:
    def test_creates_table(self, engine):
        rows = engine._db.query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='notifications'")
        assert len(rows) == 1

    def test_idempotent(self, engine):
        engine.ensure_table()


class TestNotify:
    def test_creates_notification(self, engine):
        n = engine.notify("عنوان", "محتوى", severity="warning", category="anomaly")
        assert n.title == "عنوان"
        assert n.severity == "warning"
        assert n.read is False

    def test_stores_in_db(self, engine):
        engine.notify("test", "body")
        assert engine.unread_count() == 1


class TestGetUnread:
    def test_returns_only_unread(self, engine):
        n1 = engine.notify("a", "b")
        n2 = engine.notify("c", "d")
        engine.mark_read(n1.notif_id)
        unread = engine.get_unread()
        assert len(unread) == 1
        assert unread[0].notif_id == n2.notif_id

    def test_sorted_desc(self, engine):
        engine.notify("first", "b")
        time.sleep(0.01)
        engine.notify("second", "b")
        unread = engine.get_unread()
        assert unread[0].title == "second"


class TestMarkRead:
    def test_marks_correctly(self, engine):
        n = engine.notify("test", "body")
        result = engine.mark_read(n.notif_id)
        assert result is True
        assert engine.unread_count() == 0

    def test_mark_all_read(self, engine):
        engine.notify("a", "b")
        engine.notify("c", "d")
        engine.notify("e", "f")
        count = engine.mark_all_read()
        assert count == 3
        assert engine.unread_count() == 0


class TestFilters:
    def test_get_by_category(self, engine):
        engine.notify("a", "b", category="anomaly")
        engine.notify("c", "d", category="price_change")
        result = engine.get_by_category("anomaly")
        assert len(result) == 1
        assert result[0].category == "anomaly"

    def test_get_by_severity(self, engine):
        engine.notify("a", "b", severity="critical")
        engine.notify("c", "d", severity="info")
        result = engine.get_by_severity("critical")
        assert len(result) == 1


class TestDeleteOld:
    def test_removes_old(self, engine):
        # Insert old notification directly
        old_date = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        engine._db.execute("""
            INSERT INTO notifications (notif_id, title, body, severity, category, data, created_at, read)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
        """, ("old1", "old", "old body", "info", "", "{}", old_date))
        engine.notify("new", "new body")
        count = engine.delete_old(days=30)
        assert count == 1
        assert len(engine.get_all()) == 1


class TestDailyDigest:
    def test_returns_arabic(self, engine):
        engine.notify("a", "b", severity="critical")
        engine.notify("c", "d", severity="warning")
        engine.notify("e", "f", severity="info")
        digest = engine.generate_daily_digest()
        assert "ملخص" in digest
        assert "3 تنبيه" in digest

    def test_empty_day(self, engine):
        digest = engine.generate_daily_digest()
        assert "لا توجد" in digest


class TestSerialization:
    def test_roundtrip(self):
        n = Notification(
            notif_id="abc", title="test", body="body",
            severity="info", category="system", data={"key": "val"},
            created_at=datetime(2025, 1, 1, 12), read=False,
        )
        d = n.to_dict()
        m = Notification.from_dict(d)
        assert n.notif_id == m.notif_id
        assert n.data == m.data
        assert n.severity == m.severity
