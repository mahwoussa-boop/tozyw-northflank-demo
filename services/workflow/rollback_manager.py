"""services/workflow/rollback_manager.py — مدير التراجع عن عمليات التسعير.

يتيح التراجع (Undo/Rollback) عن أيّ عملية تسعير أو إرسال ويب‌هوك:
  - يحفظ لقطة (snapshot) للحالة قبل وبعد كل عملية.
  - يدعم التراجع الفردي أو الجماعي (batch).
  - يحتفظ بتاريخ كامل قابل للاستعلام والتصفية.

⚠️ لا يستورد streamlit — خدمة خالصة قابلة للحقن.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from infrastructure.db_manager import DatabaseManager

# ──────────────────────────────────────────────────────────────
# نموذج البيانات
# ──────────────────────────────────────────────────────────────

@dataclass
class RollbackSnapshot:
    """لقطة كاملة لعملية تسعير واحدة قبل وبعد التنفيذ.

    تُخزَّن الحقول المعقّدة (before_state، after_state، webhook_payload) كـ JSON
    في أعمدة TEXT داخل SQLite.
    """

    snapshot_id: str
    entity_id: str
    action_type: str
    before_state: dict[str, Any] = field(default_factory=dict)
    after_state: dict[str, Any] = field(default_factory=dict)
    webhook_payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_reverted: bool = False
    reverted_at: Optional[datetime] = None
    batch_id: Optional[str] = None

    # ── تسلسل / عكس التسلسل ──────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """يحوّل اللقطة إلى قاموس قابل للتخزين في JSON."""
        return {
            "snapshot_id": self.snapshot_id,
            "entity_id": self.entity_id,
            "action_type": self.action_type,
            "before_state": self.before_state,
            "after_state": self.after_state,
            "webhook_payload": self.webhook_payload,
            "created_at": self.created_at.isoformat(),
            "is_reverted": self.is_reverted,
            "reverted_at": self.reverted_at.isoformat() if self.reverted_at else None,
            "batch_id": self.batch_id,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RollbackSnapshot:
        """ينشئ لقطة من قاموس (مثلاً صف قاعدة بيانات مُحوَّل)."""
        return cls(
            snapshot_id=d["snapshot_id"],
            entity_id=d["entity_id"],
            action_type=d["action_type"],
            before_state=d.get("before_state") if isinstance(d.get("before_state"), dict) else json.loads(d.get("before_state", "{}")),
            after_state=d.get("after_state") if isinstance(d.get("after_state"), dict) else json.loads(d.get("after_state", "{}")),
            webhook_payload=d.get("webhook_payload") if isinstance(d.get("webhook_payload"), dict) else json.loads(d.get("webhook_payload", "{}")),
            created_at=datetime.fromisoformat(d["created_at"]) if isinstance(d.get("created_at"), str) else d.get("created_at", datetime.now(timezone.utc)),
            is_reverted=bool(d.get("is_reverted", False)),
            reverted_at=datetime.fromisoformat(d["reverted_at"]) if d.get("reverted_at") else None,
            batch_id=d.get("batch_id"),
        )


# ──────────────────────────────────────────────────────────────
# الخدمة الرئيسية
# ──────────────────────────────────────────────────────────────

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS rollback_snapshots (
    snapshot_id    TEXT PRIMARY KEY,
    entity_id      TEXT NOT NULL,
    action_type    TEXT NOT NULL,
    before_state   TEXT NOT NULL,
    after_state    TEXT NOT NULL,
    webhook_payload TEXT DEFAULT '{}',
    batch_id       TEXT,
    created_at     TEXT NOT NULL,
    is_reverted    INTEGER DEFAULT 0,
    reverted_at    TEXT
);
"""

_CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_rb_entity   ON rollback_snapshots (entity_id);",
    "CREATE INDEX IF NOT EXISTS idx_rb_batch    ON rollback_snapshots (batch_id);",
    "CREATE INDEX IF NOT EXISTS idx_rb_created  ON rollback_snapshots (created_at);",
    "CREATE INDEX IF NOT EXISTS idx_rb_reverted ON rollback_snapshots (is_reverted);",
]


class RollbackManager:
    """مدير التراجع — يحفظ لقطات العمليات ويتيح التراجع عنها.

    يستخدم ``DatabaseManager`` للوصول إلى SQLite ولا يعتمد على أيّ
    حالة عالمية أو واجهة مستخدم.
    """

    def __init__(self, db: DatabaseManager) -> None:
        """يهيّئ المدير بمدير قاعدة بيانات موجود.

        Args:
            db: مثيل ``DatabaseManager`` للوصول إلى SQLite.
        """
        self._db: DatabaseManager = db

    # ── إنشاء الجدول ─────────────────────────────────────────

    def ensure_table(self) -> None:
        """ينشئ جدول rollback_snapshots والفهارس إن لم تكن موجودة.

        يُستدعى عادةً مرة واحدة عند بدء التطبيق أو في الاختبارات.
        """
        self._db.execute(_CREATE_TABLE_SQL)
        for idx_sql in _CREATE_INDEXES_SQL:
            self._db.execute(idx_sql)

    # ── إنشاء لقطة ───────────────────────────────────────────

    def create_snapshot(
        self,
        entity_id: str,
        action_type: str,
        before_state: dict[str, Any],
        after_state: dict[str, Any],
        *,
        webhook_payload: dict[str, Any] | None = None,
        batch_id: str | None = None,
    ) -> RollbackSnapshot:
        """ينشئ لقطة جديدة ويخزّنها في قاعدة البيانات.

        Args:
            entity_id: معرّف المنتج أو الكيان المتأثّر.
            action_type: نوع العملية (price_update | new_product | redistribution).
            before_state: حالة الكيان قبل العملية.
            after_state: حالة الكيان بعد العملية.
            webhook_payload: البيانات المُرسلة إلى Make.com (اختياري).
            batch_id: معرّف الدفعة لربط عدة لقطات معاً (اختياري).

        Returns:
            كائن ``RollbackSnapshot`` المُنشأ.
        """
        snapshot = RollbackSnapshot(
            snapshot_id=uuid.uuid4().hex[:12],
            entity_id=entity_id,
            action_type=action_type,
            before_state=before_state,
            after_state=after_state,
            webhook_payload=webhook_payload or {},
            created_at=datetime.now(timezone.utc),
            batch_id=batch_id,
        )
        self._db.execute(
            """
            INSERT INTO rollback_snapshots
                (snapshot_id, entity_id, action_type, before_state, after_state,
                 webhook_payload, batch_id, created_at, is_reverted, reverted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.snapshot_id,
                snapshot.entity_id,
                snapshot.action_type,
                json.dumps(snapshot.before_state, ensure_ascii=False),
                json.dumps(snapshot.after_state, ensure_ascii=False),
                json.dumps(snapshot.webhook_payload, ensure_ascii=False),
                snapshot.batch_id,
                snapshot.created_at.isoformat(),
                0,
                None,
            ),
        )
        return snapshot

    # ── التراجع ───────────────────────────────────────────────

    def rollback(self, snapshot_id: str) -> bool:
        """يُعلّم لقطة بأنها تمّ التراجع عنها.

        Args:
            snapshot_id: معرّف اللقطة المُراد التراجع عنها.

        Returns:
            ``True`` إذا تمّ التراجع بنجاح، ``False`` إذا كانت مفقودة أو سبق التراجع.
        """
        snapshot = self.get_snapshot(snapshot_id)
        if snapshot is None or snapshot.is_reverted:
            return False

        now = datetime.now(timezone.utc).isoformat()
        self._db.execute(
            """
            UPDATE rollback_snapshots
               SET is_reverted = 1, reverted_at = ?
             WHERE snapshot_id = ?
            """,
            (now, snapshot_id),
        )
        return True

    def rollback_batch(self, batch_id: str) -> int:
        """يتراجع عن جميع لقطات الدفعة المحدّدة.

        Args:
            batch_id: معرّف الدفعة.

        Returns:
            عدد اللقطات التي تمّ التراجع عنها فعلياً.
        """
        snapshots = self.get_by_batch(batch_id)
        count = 0
        for snap in snapshots:
            if not snap.is_reverted:
                if self.rollback(snap.snapshot_id):
                    count += 1
        return count

    # ── الاستعلامات ───────────────────────────────────────────

    def get_history(self, *, limit: int = 50) -> list[RollbackSnapshot]:
        """يعيد أحدث اللقطات مرتّبة تنازلياً حسب تاريخ الإنشاء.

        Args:
            limit: الحدّ الأقصى لعدد النتائج (افتراضي 50).

        Returns:
            قائمة كائنات ``RollbackSnapshot``.
        """
        rows = self._db.query(
            "SELECT * FROM rollback_snapshots ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [self._row_to_snapshot(row) for row in rows]

    def get_snapshot(self, snapshot_id: str) -> Optional[RollbackSnapshot]:
        """يعيد لقطة واحدة بمعرّفها أو ``None`` إذا لم تُوجد.

        Args:
            snapshot_id: معرّف اللقطة.

        Returns:
            كائن ``RollbackSnapshot`` أو ``None``.
        """
        row = self._db.query_one(
            "SELECT * FROM rollback_snapshots WHERE snapshot_id = ?",
            (snapshot_id,),
        )
        if row is None:
            return None
        return self._row_to_snapshot(row)

    def get_by_batch(self, batch_id: str) -> list[RollbackSnapshot]:
        """يعيد جميع لقطات الدفعة المحدّدة.

        Args:
            batch_id: معرّف الدفعة.

        Returns:
            قائمة كائنات ``RollbackSnapshot``.
        """
        rows = self._db.query(
            "SELECT * FROM rollback_snapshots WHERE batch_id = ? ORDER BY created_at ASC",
            (batch_id,),
        )
        return [self._row_to_snapshot(row) for row in rows]

    def get_revertable(self, *, limit: int = 20) -> list[RollbackSnapshot]:
        """يعيد اللقطات التي لم يُتراجع عنها بعد.

        Args:
            limit: الحدّ الأقصى لعدد النتائج (افتراضي 20).

        Returns:
            قائمة كائنات ``RollbackSnapshot`` غير المُتراجع عنها.
        """
        rows = self._db.query(
            "SELECT * FROM rollback_snapshots WHERE is_reverted = 0 ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [self._row_to_snapshot(row) for row in rows]

    def is_reverted(self, snapshot_id: str) -> bool:
        """يتحقّق مما إذا كانت اللقطة قد تمّ التراجع عنها.

        Args:
            snapshot_id: معرّف اللقطة.

        Returns:
            ``True`` إذا تمّ التراجع، ``False`` خلاف ذلك (بما فيها إذا لم تُوجد).
        """
        snapshot = self.get_snapshot(snapshot_id)
        if snapshot is None:
            return False
        return snapshot.is_reverted

    # ── أدوات داخلية ──────────────────────────────────────────

    @staticmethod
    def _row_to_snapshot(row) -> RollbackSnapshot:
        """يحوّل صف SQLite (sqlite3.Row) إلى كائن RollbackSnapshot."""
        return RollbackSnapshot.from_dict({
            "snapshot_id": row["snapshot_id"],
            "entity_id": row["entity_id"],
            "action_type": row["action_type"],
            "before_state": row["before_state"],
            "after_state": row["after_state"],
            "webhook_payload": row["webhook_payload"],
            "created_at": row["created_at"],
            "is_reverted": row["is_reverted"],
            "reverted_at": row["reverted_at"],
            "batch_id": row["batch_id"],
        })
