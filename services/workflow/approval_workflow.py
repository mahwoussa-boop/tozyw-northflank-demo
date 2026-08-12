"""services/workflow/approval_workflow.py — خدمة سير عمل الموافقات متعددة المستويات.

تدير سير عمل الموافقات على قرارات التسعير:
  - تقييم تلقائي للقواعد لتحديد مستوى الموافقة المطلوب.
  - دعم ثلاث مستويات: auto (آلي) / manager (مدير) / director (مدير تنفيذي).
  - تتبّع حالة كل طلب (قيد الانتظار / معتمد / مرفوض / منتهي الصلاحية).
  - إحصائيات وتقارير عن الطلبات ومتوسط وقت المعالجة.

⚠️ لا يستورد streamlit — خدمة خالصة قابلة للحقن.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from infrastructure.db_manager import DatabaseManager

# ──────────────────────────────────────────────────────────────
# نماذج البيانات
# ──────────────────────────────────────────────────────────────

# ترتيب المستويات من الأدنى إلى الأعلى
_LEVEL_PRIORITY: dict[str, int] = {"auto": 0, "manager": 1, "director": 2}


@dataclass
class ApprovalRule:
    """قاعدة موافقة واحدة تحدّد متى يُطلب مستوى موافقة أعلى.

    Attributes:
        rule_id: معرّف فريد للقاعدة.
        name: اسم القاعدة بالعربية.
        description: وصف القاعدة بالعربية.
        condition: نوع الشرط (price_change_pct | confidence | brand | amount).
        threshold: القيمة العتبية للمقارنة.
        operator: عامل المقارنة (gt | lt | gte | lte | eq | in).
        required_level: مستوى الموافقة المطلوب عند تحقّق الشرط.
        enabled: هل القاعدة مفعّلة؟
    """

    rule_id: str
    name: str
    description: str
    condition: str
    threshold: float
    operator: str
    required_level: str
    enabled: bool = True


@dataclass
class ApprovalRequest:
    """طلب موافقة على تغيير تسعير.

    يُنشأ تلقائياً عند تقييم تغيير سعري ويحتوي على كل المعلومات اللازمة
    للمُعتمِد لاتخاذ القرار.

    Attributes:
        request_id: معرّف فريد (أول 12 حرف من UUID hex).
        product_name: اسم المنتج.
        brand: العلامة التجارية.
        current_price: السعر الحالي.
        suggested_price: السعر المقترح.
        price_change_pct: نسبة التغيير المئوية.
        confidence: درجة الثقة في السعر المقترح.
        required_level: مستوى الموافقة المطلوب.
        status: حالة الطلب.
        triggered_rules: أسماء القواعد التي أدت لهذا المستوى.
        created_at: تاريخ الإنشاء.
        resolved_at: تاريخ الحل (موافقة/رفض).
        resolved_by: من قام بالحل.
        notes: ملاحظات إضافية.
    """

    request_id: str
    product_name: str
    brand: str
    current_price: float
    suggested_price: float
    price_change_pct: float
    confidence: float
    required_level: str
    status: str
    triggered_rules: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: Optional[datetime] = None
    resolved_by: str = ""
    notes: str = ""

    def to_dict(self) -> dict:
        """يحوّل طلب الموافقة إلى قاموس قابل للتسلسل كـ JSON."""
        return {
            "request_id": self.request_id,
            "product_name": self.product_name,
            "brand": self.brand,
            "current_price": self.current_price,
            "suggested_price": self.suggested_price,
            "price_change_pct": self.price_change_pct,
            "confidence": self.confidence,
            "required_level": self.required_level,
            "status": self.status,
            "triggered_rules": self.triggered_rules,
            "created_at": self.created_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "resolved_by": self.resolved_by,
            "notes": self.notes,
        }


# ──────────────────────────────────────────────────────────────
# الخدمة الرئيسية
# ──────────────────────────────────────────────────────────────

_CREATE_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS approval_requests (
    request_id      TEXT PRIMARY KEY,
    product_name    TEXT NOT NULL,
    brand           TEXT DEFAULT '',
    current_price   REAL,
    suggested_price REAL,
    price_change_pct REAL,
    confidence      REAL,
    required_level  TEXT,
    status          TEXT DEFAULT 'pending',
    triggered_rules TEXT DEFAULT '[]',
    created_at      TEXT NOT NULL,
    resolved_at     TEXT,
    resolved_by     TEXT DEFAULT '',
    notes           TEXT DEFAULT ''
);
"""

_CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_apr_status ON approval_requests(status);",
    "CREATE INDEX IF NOT EXISTS idx_apr_level ON approval_requests(required_level);",
    "CREATE INDEX IF NOT EXISTS idx_apr_created ON approval_requests(created_at);",
]


class ApprovalWorkflow:
    """خدمة سير عمل الموافقات متعددة المستويات لقرارات التسعير.

    تقيّم قواعد محدّدة مسبقاً (أو مخصّصة) لتحديد ما إذا كان تغيير السعر
    يحتاج موافقة يدوية ومن أيّ مستوى إداري.

    Args:
        db: مدير قاعدة البيانات للتخزين والاستعلام.
    """

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    # ── إنشاء الجدول ──────────────────────────────────────────

    def ensure_table(self) -> None:
        """ينشئ جدول approval_requests إن لم يكن موجوداً مع الفهارس."""
        self._db.execute(_CREATE_TABLE_SQL)
        for idx_sql in _CREATE_INDEXES_SQL:
            self._db.execute(idx_sql)

    # ── القواعد الافتراضية ─────────────────────────────────────

    def _default_rules(self) -> list[ApprovalRule]:
        """يعيد القواعد المدمجة لتقييم مستوى الموافقة.

        القواعد الافتراضية:
          1. تغيير السعر > 15%: يتطلب مدير.
          2. تغيير السعر > 30%: يتطلب مدير تنفيذي.
          3. درجة الثقة < 0.7: يتطلب مدير.
          4. مبلغ التغيير > 500 ريال: يتطلب مدير.
          5. مبلغ التغيير > 1000 ريال: يتطلب مدير تنفيذي.
        """
        return [
            ApprovalRule(
                rule_id="R001",
                name="تغيير سعر كبير",
                description="تغيير السعر يتجاوز 15% يتطلب موافقة مدير",
                condition="price_change_pct",
                threshold=15.0,
                operator="gt",
                required_level="manager",
            ),
            ApprovalRule(
                rule_id="R002",
                name="تغيير سعر ضخم",
                description="تغيير السعر يتجاوز 30% يتطلب موافقة مدير تنفيذي",
                condition="price_change_pct",
                threshold=30.0,
                operator="gt",
                required_level="director",
            ),
            ApprovalRule(
                rule_id="R003",
                name="ثقة منخفضة",
                description="درجة الثقة أقل من 0.7 يتطلب موافقة مدير",
                condition="confidence",
                threshold=0.7,
                operator="lt",
                required_level="manager",
            ),
            ApprovalRule(
                rule_id="R004",
                name="مبلغ تغيير كبير",
                description="مبلغ التغيير يتجاوز 500 ريال يتطلب موافقة مدير",
                condition="amount",
                threshold=500.0,
                operator="gt",
                required_level="manager",
            ),
            ApprovalRule(
                rule_id="R005",
                name="مبلغ تغيير ضخم",
                description="مبلغ التغيير يتجاوز 1000 ريال يتطلب موافقة مدير تنفيذي",
                condition="amount",
                threshold=1000.0,
                operator="gt",
                required_level="director",
            ),
        ]

    # ── تقييم القواعد ──────────────────────────────────────────

    @staticmethod
    def _compare(value: float, operator: str, threshold: float) -> bool:
        """يقارن قيمة مع عتبة باستخدام العامل المحدّد.

        Args:
            value: القيمة المراد مقارنتها.
            operator: عامل المقارنة (gt|lt|gte|lte|eq|in).
            threshold: القيمة العتبية.

        Returns:
            نتيجة المقارنة.
        """
        if operator == "gt":
            return value > threshold
        if operator == "lt":
            return value < threshold
        if operator == "gte":
            return value >= threshold
        if operator == "lte":
            return value <= threshold
        if operator == "eq":
            return value == threshold
        return False

    def evaluate_approval_level(
        self,
        *,
        product_name: str,
        brand: str = "",
        current_price: float,
        suggested_price: float,
        confidence: float,
        custom_rules: Optional[list[ApprovalRule]] = None,
    ) -> ApprovalRequest:
        """يقيّم جميع القواعد ويحدّد مستوى الموافقة المطلوب.

        يحسب نسبة التغيير ومبلغه، ثم يفحص كل قاعدة مفعّلة.
        المستوى الأعلى من بين القواعد المُحقَّقة هو المطلوب.
        إذا لم تتحقّق أيّ قاعدة (أو كلها auto)، يُعتمَد الطلب تلقائياً.

        Args:
            product_name: اسم المنتج.
            brand: العلامة التجارية.
            current_price: السعر الحالي.
            suggested_price: السعر المقترح.
            confidence: درجة الثقة.
            custom_rules: قواعد مخصّصة (تُستخدم بدلاً من الافتراضية).

        Returns:
            طلب الموافقة الذي تم إنشاؤه وتخزينه.
        """
        rules = custom_rules if custom_rules is not None else self._default_rules()

        # حساب نسبة ومبلغ التغيير
        if current_price != 0:
            price_change_pct = abs(
                (suggested_price - current_price) / current_price * 100
            )
        else:
            price_change_pct = 0.0

        amount_change = abs(suggested_price - current_price)

        # تقييم القواعد
        highest_level = "auto"
        triggered_rules: list[str] = []

        for rule in rules:
            if not rule.enabled:
                continue

            # تحديد القيمة المناسبة حسب الشرط
            if rule.condition == "price_change_pct":
                value = price_change_pct
            elif rule.condition == "confidence":
                value = confidence
            elif rule.condition == "amount":
                value = amount_change
            else:
                continue

            if self._compare(value, rule.operator, rule.threshold):
                triggered_rules.append(rule.name)
                if _LEVEL_PRIORITY.get(rule.required_level, 0) > _LEVEL_PRIORITY.get(
                    highest_level, 0
                ):
                    highest_level = rule.required_level

        # تحديد الحالة
        status = "approved" if highest_level == "auto" else "pending"

        request = ApprovalRequest(
            request_id=uuid.uuid4().hex[:12],
            product_name=product_name,
            brand=brand,
            current_price=current_price,
            suggested_price=suggested_price,
            price_change_pct=round(price_change_pct, 2),
            confidence=confidence,
            required_level=highest_level,
            status=status,
            triggered_rules=triggered_rules,
            created_at=datetime.now(timezone.utc),
            resolved_at=datetime.now(timezone.utc) if status == "approved" else None,
            resolved_by="system" if status == "approved" else "",
        )

        # تخزين في قاعدة البيانات
        self._db.execute(
            """INSERT INTO approval_requests
               (request_id, product_name, brand, current_price, suggested_price,
                price_change_pct, confidence, required_level, status,
                triggered_rules, created_at, resolved_at, resolved_by, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                request.request_id,
                request.product_name,
                request.brand,
                request.current_price,
                request.suggested_price,
                request.price_change_pct,
                request.confidence,
                request.required_level,
                request.status,
                json.dumps(request.triggered_rules, ensure_ascii=False),
                request.created_at.isoformat(),
                request.resolved_at.isoformat() if request.resolved_at else None,
                request.resolved_by,
                request.notes,
            ),
        )

        return request

    # ── إجراءات الموافقة والرفض ────────────────────────────────

    def approve(
        self, request_id: str, *, approved_by: str = "manager", notes: str = ""
    ) -> bool:
        """يعتمد طلب موافقة قيد الانتظار.

        Args:
            request_id: معرّف الطلب.
            approved_by: اسم المُعتمِد.
            notes: ملاحظات إضافية.

        Returns:
            True إذا تم الاعتماد بنجاح، False إذا لم يُعثر على الطلب.
        """
        existing = self._db.query_one(
            "SELECT request_id FROM approval_requests WHERE request_id = ? AND status = 'pending'",
            (request_id,),
        )
        if existing is None:
            return False

        now = datetime.now(timezone.utc).isoformat()
        self._db.execute(
            """UPDATE approval_requests
               SET status = 'approved', resolved_at = ?, resolved_by = ?, notes = ?
               WHERE request_id = ?""",
            (now, approved_by, notes, request_id),
        )
        return True

    def reject(
        self, request_id: str, *, rejected_by: str = "manager", notes: str = ""
    ) -> bool:
        """يرفض طلب موافقة قيد الانتظار.

        Args:
            request_id: معرّف الطلب.
            rejected_by: اسم الرافض.
            notes: سبب الرفض.

        Returns:
            True إذا تم الرفض بنجاح، False إذا لم يُعثر على الطلب.
        """
        existing = self._db.query_one(
            "SELECT request_id FROM approval_requests WHERE request_id = ? AND status = 'pending'",
            (request_id,),
        )
        if existing is None:
            return False

        now = datetime.now(timezone.utc).isoformat()
        self._db.execute(
            """UPDATE approval_requests
               SET status = 'rejected', resolved_at = ?, resolved_by = ?, notes = ?
               WHERE request_id = ?""",
            (now, rejected_by, notes, request_id),
        )
        return True

    # ── استعلامات ──────────────────────────────────────────────

    def get_pending(
        self, *, level: Optional[str] = None
    ) -> list[ApprovalRequest]:
        """يعيد طلبات الموافقة قيد الانتظار.

        Args:
            level: تصفية حسب مستوى الموافقة المطلوب (اختياري).

        Returns:
            قائمة طلبات الموافقة المعلّقة.
        """
        if level is not None:
            rows = self._db.query(
                "SELECT * FROM approval_requests WHERE status = 'pending' AND required_level = ?",
                (level,),
            )
        else:
            rows = self._db.query(
                "SELECT * FROM approval_requests WHERE status = 'pending'"
            )
        return [self._row_to_request(row) for row in rows]

    def get_request(self, request_id: str) -> Optional[ApprovalRequest]:
        """يعيد طلب موافقة بمعرّفه.

        Args:
            request_id: معرّف الطلب.

        Returns:
            طلب الموافقة أو None إذا لم يُعثر عليه.
        """
        row = self._db.query_one(
            "SELECT * FROM approval_requests WHERE request_id = ?",
            (request_id,),
        )
        if row is None:
            return None
        return self._row_to_request(row)

    def get_stats(self) -> dict:
        """يعيد إحصائيات الطلبات.

        Returns:
            قاموس يحتوي على:
              - pending: عدد الطلبات قيد الانتظار.
              - approved: عدد الطلبات المعتمدة.
              - rejected: عدد الطلبات المرفوضة.
              - avg_resolution_time_hours: متوسط وقت المعالجة بالساعات.
        """
        rows = self._db.query(
            """SELECT status, COUNT(*) as cnt FROM approval_requests GROUP BY status"""
        )
        counts: dict[str, int] = {"pending": 0, "approved": 0, "rejected": 0, "expired": 0}
        for row in rows:
            counts[row["status"]] = row["cnt"]

        # متوسط وقت المعالجة للطلبات المحلولة
        resolved_rows = self._db.query(
            """SELECT created_at, resolved_at FROM approval_requests
               WHERE resolved_at IS NOT NULL AND status IN ('approved', 'rejected')"""
        )
        avg_hours = 0.0
        if resolved_rows:
            total_seconds = 0.0
            for r in resolved_rows:
                created = datetime.fromisoformat(r["created_at"])
                resolved = datetime.fromisoformat(r["resolved_at"])
                total_seconds += (resolved - created).total_seconds()
            avg_hours = round(total_seconds / len(resolved_rows) / 3600, 2)

        return {
            "pending": counts["pending"],
            "approved": counts["approved"],
            "rejected": counts["rejected"],
            "avg_resolution_time_hours": avg_hours,
        }

    # ── انتهاء الصلاحية ────────────────────────────────────────

    def expire_old(self, *, hours: int = 48) -> int:
        """يُنهي صلاحية الطلبات المعلّقة الأقدم من عدد ساعات محدّد.

        Args:
            hours: عدد الساعات قبل انتهاء الصلاحية (افتراضي: 48).

        Returns:
            عدد الطلبات التي انتهت صلاحيتها.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        now = datetime.now(timezone.utc).isoformat()

        cursor = self._db.execute(
            """UPDATE approval_requests
               SET status = 'expired', resolved_at = ?, resolved_by = 'system', notes = 'انتهت الصلاحية تلقائياً'
               WHERE status = 'pending' AND created_at < ?""",
            (now, cutoff),
        )
        return cursor.rowcount

    # ── أدوات داخلية ───────────────────────────────────────────

    @staticmethod
    def _row_to_request(row) -> ApprovalRequest:
        """يحوّل صف قاعدة بيانات إلى كائن ApprovalRequest.

        Args:
            row: صف من sqlite3.Row.

        Returns:
            كائن ApprovalRequest.
        """
        triggered = row["triggered_rules"]
        if isinstance(triggered, str):
            triggered = json.loads(triggered)

        resolved_at = None
        if row["resolved_at"]:
            resolved_at = datetime.fromisoformat(row["resolved_at"])

        return ApprovalRequest(
            request_id=row["request_id"],
            product_name=row["product_name"],
            brand=row["brand"] or "",
            current_price=row["current_price"],
            suggested_price=row["suggested_price"],
            price_change_pct=row["price_change_pct"],
            confidence=row["confidence"],
            required_level=row["required_level"],
            status=row["status"],
            triggered_rules=triggered,
            created_at=datetime.fromisoformat(row["created_at"]),
            resolved_at=resolved_at,
            resolved_by=row["resolved_by"] or "",
            notes=row["notes"] or "",
        )
