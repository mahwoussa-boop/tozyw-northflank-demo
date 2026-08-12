"""services/workflow/autopilot.py — محرّك الطيار الآلي للتسعير.

يُقيّم قرارات التسعير تلقائياً وفق مجموعة قواعد أمان صارمة:
  - حدّ أدنى للثقة المُعايَرة.
  - حدّ أقصى لنسبة تغيير السعر.
  - استبعاد علامات تجارية محدّدة.
  - حدّ يومي لعدد الإجراءات التلقائية.
  - اشتراط إجماع من السّرب (swarm).

يسجّل كل إجراء (تنفيذ / مراجعة / حظر) في جدول ``autopilot_log``
لتتبّع كامل يدعم التدقيق والمراجعة.

⚠️ لا يستورد streamlit — خدمة خالصة قابلة للحقن.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from infrastructure.db_manager import DatabaseManager
from services.workflow.rollback_manager import RollbackManager


# ──────────────────────────────────────────────────────────────
# نماذج البيانات
# ──────────────────────────────────────────────────────────────

@dataclass
class AutoPilotConfig:
    """إعدادات محرّك الطيار الآلي.

    Attributes:
        enabled: هل الطيار الآلي مُفعَّل؟
        min_confidence: الحدّ الأدنى للثقة المُعايَرة المطلوبة.
        max_price_change_pct: الحدّ الأقصى لنسبة تغيير السعر (%).
        excluded_brands: علامات تجارية مستثناة من التنفيذ التلقائي.
        max_daily_actions: الحدّ الأقصى للإجراءات التلقائية اليومية.
        require_consensus: هل يُشترط إجماع كامل من السّرب؟
    """

    enabled: bool = False
    min_confidence: float = 0.90
    max_price_change_pct: float = 10.0
    excluded_brands: list[str] = field(default_factory=list)
    max_daily_actions: int = 100
    require_consensus: bool = True


@dataclass
class AutoPilotVerdict:
    """نتيجة تقييم الطيار الآلي لقرار تسعير واحد.

    Attributes:
        can_execute: هل يمكن تنفيذ القرار تلقائياً؟
        reason: شرح بالعربية لسبب القرار.
        approval_level: مستوى الموافقة (auto | review | manual).
        blocked_by: قائمة أسماء القواعد التي حظرت القرار.
    """

    can_execute: bool
    reason: str
    approval_level: str
    blocked_by: list[str] = field(default_factory=list)


@dataclass
class AutoPilotReport:
    """تقرير ملخّص لدورة تقييم كاملة.

    Attributes:
        total_evaluated: إجمالي العناصر التي تمّ تقييمها.
        auto_executed: عدد العناصر المُنفّذة تلقائياً.
        sent_to_review: عدد العناصر المُحالة للمراجعة.
        blocked: عدد العناصر المحظورة.
        details: تفاصيل كل عنصر.
    """

    total_evaluated: int = 0
    auto_executed: int = 0
    sent_to_review: int = 0
    blocked: int = 0
    details: list[dict] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────
# SQL — إنشاء جدول السجل
# ──────────────────────────────────────────────────────────────

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS autopilot_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name TEXT NOT NULL,
    action       TEXT NOT NULL,
    price        REAL DEFAULT 0.0,
    details      TEXT DEFAULT '{}',
    created_at   TEXT NOT NULL
);
"""

_CREATE_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_ap_created ON autopilot_log (created_at);"
)


# ──────────────────────────────────────────────────────────────
# الخدمة الرئيسية
# ──────────────────────────────────────────────────────────────

class AutoPilotEngine:
    """محرّك الطيار الآلي — يُقيّم قرارات التسعير ويسجّل الإجراءات.

    يُحقن بـ ``AutoPilotConfig`` و ``RollbackManager`` و ``DatabaseManager``
    عبر المُنشئ ولا يعتمد على أيّ حالة عالمية.
    """

    def __init__(
        self,
        config: AutoPilotConfig,
        rollback: RollbackManager,
        db: DatabaseManager,
    ) -> None:
        """يهيّئ المحرّك بالتكوين والاعتماديات.

        Args:
            config: إعدادات الطيار الآلي.
            rollback: مدير التراجع لحفظ لقطات العمليات.
            db: مدير قاعدة البيانات للتسجيل.
        """
        self._config: AutoPilotConfig = config
        self._rollback: RollbackManager = rollback
        self._db: DatabaseManager = db

    # ── إنشاء الجدول ─────────────────────────────────────────

    def ensure_table(self) -> None:
        """ينشئ جدول autopilot_log والفهرس إن لم يكونا موجودَين.

        يُستدعى عادةً مرة واحدة عند بدء التطبيق أو في الاختبارات.
        """
        self._db.execute(_CREATE_TABLE_SQL)
        self._db.execute(_CREATE_INDEX_SQL)

    # ── التقييم ───────────────────────────────────────────────

    def evaluate(
        self,
        *,
        product_name: str,
        brand: str = "",
        current_price: float,
        suggested_price: float,
        confidence: float,
        consensus_type: str = "unanimous",
    ) -> AutoPilotVerdict:
        """يُقيّم قرار تسعير واحد وفق جميع قواعد الأمان.

        القواعد المُطبَّقة بالترتيب:
            1. هل الطيار الآلي مُفعَّل؟
            2. هل الثقة >= الحدّ الأدنى؟
            3. هل نسبة تغيير السعر <= الحدّ الأقصى؟
            4. هل العلامة التجارية غير مستبعدة؟
            5. هل الحدّ اليومي لم يُتجاوز؟
            6. هل يوجد إجماع (إن كان مطلوباً)؟

        Args:
            product_name: اسم المنتج.
            brand: العلامة التجارية (اختياري).
            current_price: السعر الحالي.
            suggested_price: السعر المقترح.
            confidence: مستوى الثقة المُعايَر (0.0 – 1.0).
            consensus_type: نوع الإجماع (unanimous | majority | ...).

        Returns:
            كائن ``AutoPilotVerdict`` يحتوي القرار والتفاصيل.
        """
        blocked_by: list[str] = []

        # 1. هل الطيار الآلي مُفعَّل؟
        if not self._config.enabled:
            blocked_by.append("autopilot_disabled")

        # 2. هل الثقة كافية؟
        if confidence < self._config.min_confidence:
            blocked_by.append("low_confidence")

        # 3. هل نسبة تغيير السعر مقبولة؟
        if current_price > 0:
            change_pct = abs(suggested_price - current_price) / current_price * 100
        else:
            change_pct = 0.0 if suggested_price == 0 else 100.0

        if change_pct > self._config.max_price_change_pct:
            blocked_by.append("price_change_too_large")

        # 4. هل العلامة التجارية غير مستبعدة؟
        if brand and brand in self._config.excluded_brands:
            blocked_by.append("excluded_brand")

        # 5. هل الحدّ اليومي لم يُتجاوز؟
        daily_count = self.get_daily_count()
        if daily_count >= self._config.max_daily_actions:
            blocked_by.append("daily_limit_exceeded")

        # 6. هل يوجد إجماع؟
        if self._config.require_consensus and consensus_type != "unanimous":
            blocked_by.append("no_consensus")

        # ── بناء الحكم ────────────────────────────────────────
        if not blocked_by:
            return AutoPilotVerdict(
                can_execute=True,
                reason="جميع القواعد مُستوفاة — يمكن التنفيذ التلقائي",
                approval_level="auto",
                blocked_by=[],
            )

        # تحديد مستوى الموافقة بناءً على شدة الحظر
        severe = {"autopilot_disabled", "daily_limit_exceeded", "excluded_brand"}
        if severe & set(blocked_by):
            level = "manual"
        else:
            level = "review"

        reasons = {
            "autopilot_disabled": "الطيار الآلي مُعطَّل",
            "low_confidence": f"الثقة ({confidence:.2f}) أقل من الحدّ الأدنى ({self._config.min_confidence})",
            "price_change_too_large": f"تغيير السعر ({change_pct:.1f}%) يتجاوز الحدّ ({self._config.max_price_change_pct}%)",
            "excluded_brand": f"العلامة التجارية «{brand}» مستبعدة",
            "daily_limit_exceeded": f"تمّ تجاوز الحدّ اليومي ({self._config.max_daily_actions})",
            "no_consensus": "لا يوجد إجماع كامل من السّرب",
        }
        reason_parts = [reasons.get(b, b) for b in blocked_by]

        return AutoPilotVerdict(
            can_execute=False,
            reason=" | ".join(reason_parts),
            approval_level=level,
            blocked_by=list(blocked_by),
        )

    # ── تسجيل الإجراءات ───────────────────────────────────────

    def record_action(
        self,
        product_name: str,
        action: str,
        *,
        price: float = 0.0,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        """يسجّل إجراء الطيار الآلي في جدول autopilot_log.

        Args:
            product_name: اسم المنتج.
            action: نوع الإجراء (auto_executed | sent_to_review | blocked).
            price: السعر المُطبَّق (افتراضي 0.0).
            details: تفاصيل إضافية (تُخزَّن كـ JSON).
        """
        now = datetime.now(timezone.utc).isoformat()
        details_json = json.dumps(details or {}, ensure_ascii=False)

        self._db.execute(
            """
            INSERT INTO autopilot_log (product_name, action, price, details, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (product_name, action, price, details_json, now),
        )

    # ── الإحصائيات اليومية ─────────────────────────────────────

    def get_daily_count(self) -> int:
        """يعيد عدد الإجراءات التلقائية المُنفّذة اليوم (auto_executed فقط).

        Returns:
            عدد الإجراءات التلقائية المنفّذة في اليوم الحالي (UTC).
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = self._db.query_one(
            """
            SELECT COUNT(*) AS cnt
              FROM autopilot_log
             WHERE action = 'auto_executed'
               AND created_at >= ?
            """,
            (today,),
        )
        return int(row["cnt"]) if row else 0

    def get_daily_stats(self) -> dict[str, int]:
        """يعيد إحصائيات اليوم الكاملة.

        Returns:
            قاموس يحتوي:
            - auto_executed: عدد الإجراءات التلقائية.
            - reviewed: عدد المُحالة للمراجعة.
            - blocked: عدد المحظورة.
            - remaining_budget: الميزانية المتبقية لليوم.
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        rows = self._db.query(
            """
            SELECT action, COUNT(*) AS cnt
              FROM autopilot_log
             WHERE created_at >= ?
             GROUP BY action
            """,
            (today,),
        )

        counts: dict[str, int] = {}
        for row in rows:
            counts[row["action"]] = int(row["cnt"])

        auto_executed = counts.get("auto_executed", 0)
        reviewed = counts.get("sent_to_review", 0)
        blocked = counts.get("blocked", 0)

        return {
            "auto_executed": auto_executed,
            "reviewed": reviewed,
            "blocked": blocked,
            "remaining_budget": self._config.max_daily_actions - auto_executed,
        }

    # ── تحديث الإعدادات ────────────────────────────────────────

    def update_config(self, **kwargs: Any) -> AutoPilotConfig:
        """يُحدّث إعدادات الطيار الآلي ويعيد التكوين الجديد.

        Args:
            **kwargs: أزواج مفتاح/قيمة لتحديثها في ``AutoPilotConfig``.

        Returns:
            كائن ``AutoPilotConfig`` المُحدَّث.

        Raises:
            AttributeError: إذا كان المفتاح غير موجود في التكوين.
        """
        for key, value in kwargs.items():
            if not hasattr(self._config, key):
                raise AttributeError(
                    f"الحقل '{key}' غير موجود في AutoPilotConfig"
                )
            setattr(self._config, key, value)
        return self._config
