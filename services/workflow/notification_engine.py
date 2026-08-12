"""services/workflow/notification_engine.py — محرك التنبيهات الذكية."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from core.product_entity import _utcnow
from infrastructure.db_manager import DatabaseManager


@dataclass
class Notification:
    """تنبيه واحد."""
    notif_id: str
    title: str
    body: str
    severity: str       # info | warning | critical
    category: str       # price_change | anomaly | schedule | autopilot | system
    data: dict[str, Any]
    created_at: datetime
    read: bool = False
    read_at: Optional[datetime] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "notif_id": self.notif_id,
            "title": self.title,
            "body": self.body,
            "severity": self.severity,
            "category": self.category,
            "data": self.data,
            "created_at": self.created_at.isoformat(),
            "read": self.read,
            "read_at": self.read_at.isoformat() if self.read_at else None,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Notification":
        ca = d.get("created_at", "")
        if isinstance(ca, str) and ca:
            ca = datetime.fromisoformat(ca)
        ra = d.get("read_at")
        if isinstance(ra, str) and ra:
            ra = datetime.fromisoformat(ra)
        else:
            ra = None
        data = d.get("data", {})
        if isinstance(data, str):
            data = json.loads(data)
        return cls(
            notif_id=d["notif_id"],
            title=d["title"],
            body=d["body"],
            severity=d.get("severity", "info"),
            category=d.get("category", ""),
            data=data,
            created_at=ca,
            read=bool(d.get("read", False)),
            read_at=ra,
        )


class NotificationEngine:
    """يدير التنبيهات الذكية للمستخدم."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    def ensure_table(self) -> None:
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                notif_id   TEXT PRIMARY KEY,
                title      TEXT NOT NULL,
                body       TEXT NOT NULL,
                severity   TEXT DEFAULT 'info',
                category   TEXT DEFAULT '',
                data       TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                read       INTEGER DEFAULT 0,
                read_at    TEXT
            )
        """)
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_notif_read ON notifications(read)")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_notif_severity ON notifications(severity)")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_notif_created ON notifications(created_at)")

    def notify(
        self, title: str, body: str, *,
        severity: str = "info", category: str = "",
        data: dict = None,
    ) -> Notification:
        """ينشئ تنبيهاً ويخزنه."""
        notif = Notification(
            notif_id=uuid.uuid4().hex[:12],
            title=title,
            body=body,
            severity=severity,
            category=category,
            data=data or {},
            created_at=_utcnow(),
        )
        self._db.execute("""
            INSERT INTO notifications
                (notif_id, title, body, severity, category, data, created_at, read)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
        """, (
            notif.notif_id, notif.title, notif.body,
            notif.severity, notif.category,
            json.dumps(notif.data, ensure_ascii=False),
            notif.created_at.isoformat(),
        ))
        return notif

    def get_unread(self, *, limit: int = 20) -> list[Notification]:
        rows = self._db.query(
            "SELECT * FROM notifications WHERE read = 0 ORDER BY created_at DESC LIMIT ?",
            (limit,))
        return [self._row_to_notif(r) for r in rows]

    def get_all(self, *, limit: int = 50) -> list[Notification]:
        rows = self._db.query(
            "SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?", (limit,))
        return [self._row_to_notif(r) for r in rows]

    def get_by_category(self, category: str, *, limit: int = 20) -> list[Notification]:
        rows = self._db.query(
            "SELECT * FROM notifications WHERE category = ? ORDER BY created_at DESC LIMIT ?",
            (category, limit))
        return [self._row_to_notif(r) for r in rows]

    def get_by_severity(self, severity: str, *, limit: int = 20) -> list[Notification]:
        rows = self._db.query(
            "SELECT * FROM notifications WHERE severity = ? ORDER BY created_at DESC LIMIT ?",
            (severity, limit))
        return [self._row_to_notif(r) for r in rows]

    def mark_read(self, notif_id: str) -> bool:
        now = _utcnow().isoformat()
        self._db.execute(
            "UPDATE notifications SET read = 1, read_at = ? WHERE notif_id = ? AND read = 0",
            (now, notif_id))
        row = self._db.query("SELECT read FROM notifications WHERE notif_id = ?", (notif_id,))
        return bool(row and row[0]["read"])

    def mark_all_read(self) -> int:
        before = self.unread_count()
        now = _utcnow().isoformat()
        self._db.execute("UPDATE notifications SET read = 1, read_at = ? WHERE read = 0", (now,))
        return before

    def mark_category_read(self, category: str) -> int:
        """يطوي (read=1) كل إشعارات فئة غير مقروءة دون حذف — كي يظهر أحدث رقم فقط.

        يُستدعى قبل توليد إشعارات فئة جديدة فلا تتراكم النسخ القديمة. لا يحذف أي صفّ
        (القديم يبقى تاريخاً بـ read=1). يُعيد عدد الصفوف التي طُويت.
        """
        rows = self._db.query(
            "SELECT COUNT(*) as cnt FROM notifications WHERE category = ? AND read = 0",
            (category,))
        count = int(rows[0]["cnt"]) if rows else 0
        now = _utcnow().isoformat()
        self._db.execute(
            "UPDATE notifications SET read = 1, read_at = ? WHERE category = ? AND read = 0",
            (now, category))
        return count

    def unread_count(self) -> int:
        rows = self._db.query("SELECT COUNT(*) as cnt FROM notifications WHERE read = 0")
        return int(rows[0]["cnt"]) if rows else 0

    def delete_old(self, *, days: int = 30) -> int:
        cutoff = (_utcnow() - timedelta(days=days)).isoformat()
        before = self._db.query("SELECT COUNT(*) as cnt FROM notifications WHERE created_at < ?", (cutoff,))
        count = int(before[0]["cnt"]) if before else 0
        self._db.execute("DELETE FROM notifications WHERE created_at < ?", (cutoff,))
        return count

    def generate_daily_digest(self) -> str:
        """يولّد ملخصاً يومياً بالعربية."""
        today = _utcnow().date().isoformat()
        rows = self._db.query(
            "SELECT severity, COUNT(*) as cnt FROM notifications WHERE created_at >= ? GROUP BY severity",
            (today,))
        if not rows:
            return "📊 ملخص اليوم: لا توجد تنبيهات جديدة"

        total = sum(int(r["cnt"]) for r in rows)
        counts = {r["severity"]: int(r["cnt"]) for r in rows}
        critical = counts.get("critical", 0)
        warning = counts.get("warning", 0)
        info = counts.get("info", 0)
        return f"📊 ملخص اليوم: {total} تنبيه | {critical} عاجل | {warning} تحذير | {info} عادي"

    @staticmethod
    def _row_to_notif(r) -> Notification:
        row = dict(r)
        data = row.get("data", "{}")
        if isinstance(data, str):
            data = json.loads(data) if data else {}
        ra = row.get("read_at")
        if ra and isinstance(ra, str):
            ra = datetime.fromisoformat(ra)
        else:
            ra = None
        return Notification(
            notif_id=row["notif_id"], title=row["title"], body=row["body"],
            severity=row.get("severity", "info"), category=row.get("category", ""),
            data=data, created_at=datetime.fromisoformat(row["created_at"]),
            read=bool(row.get("read", 0)), read_at=ra,
        )
