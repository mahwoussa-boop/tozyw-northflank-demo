"""services/learning/feedback_collector.py — جامع تقييمات المستخدم لقرارات التسعير.

يجمع ويحلّل تقييمات المستخدم على قرارات التسعير بالذكاء الاصطناعي لتمكين التعلّم المستمر:
- تسجيل كل تقييم (موافقة / رفض / تعديل) مع حساب الانحراف تلقائياً.
- إحصائيات دقة عامة ولكل علامة تجارية ولكل شريحة سعرية.
- نافذة زمنية قابلة للتخصيص لتحليل الأداء.

⚠️ لا يستورد streamlit — خدمة خالصة قابلة للحقن.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from infrastructure.db_manager import DatabaseManager


# ---------------------------------------------------------------------------
# دوال مساعدة
# ---------------------------------------------------------------------------

def compute_price_tier(price: float) -> str:
    """يحدّد شريحة السعر: budget / mid / premium / luxury.

    Args:
        price: السعر المراد تصنيفه.

    Returns:
        اسم الشريحة السعرية.
    """
    if price < 100:
        return "budget"
    elif price <= 300:
        return "mid"
    elif price <= 700:
        return "premium"
    else:
        return "luxury"


def compute_deviation(ai_price: float, user_price: float) -> float:
    """يحسب نسبة انحراف سعر المستخدم عن سعر الذكاء الاصطناعي.

    Args:
        ai_price: السعر المقترح من الذكاء الاصطناعي.
        user_price: السعر النهائي الذي اختاره المستخدم.

    Returns:
        نسبة الانحراف المئوية. يُعيد 0.0 إذا كان ai_price صفراً.
    """
    if ai_price == 0.0:
        return 0.0
    return (user_price - ai_price) / ai_price * 100.0


# ---------------------------------------------------------------------------
# نماذج البيانات
# ---------------------------------------------------------------------------

@dataclass
class FeedbackRecord:
    """سجل تقييم واحد لقرار تسعير بالذكاء الاصطناعي."""

    record_id: str
    decision_id: int
    product_name: str
    brand: Optional[str]
    section: str
    ai_suggested_price: float
    ai_confidence: float
    user_action: str
    user_final_price: float
    deviation_pct: float
    price_range_tier: str
    created_at: datetime

    def to_dict(self) -> dict:
        """يحوّل السجل إلى قاموس قابل للتسلسل بـ JSON."""
        return {
            "record_id": self.record_id,
            "decision_id": self.decision_id,
            "product_name": self.product_name,
            "brand": self.brand,
            "section": self.section,
            "ai_suggested_price": self.ai_suggested_price,
            "ai_confidence": self.ai_confidence,
            "user_action": self.user_action,
            "user_final_price": self.user_final_price,
            "deviation_pct": self.deviation_pct,
            "price_range_tier": self.price_range_tier,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> FeedbackRecord:
        """ينشئ سجلاً من قاموس (مثلاً من JSON المُخزَّن)."""
        created = d["created_at"]
        if isinstance(created, str):
            created = datetime.fromisoformat(created)
        return cls(
            record_id=d["record_id"],
            decision_id=d["decision_id"],
            product_name=d["product_name"],
            brand=d.get("brand"),
            section=d["section"],
            ai_suggested_price=d["ai_suggested_price"],
            ai_confidence=d["ai_confidence"],
            user_action=d["user_action"],
            user_final_price=d["user_final_price"],
            deviation_pct=d["deviation_pct"],
            price_range_tier=d["price_range_tier"],
            created_at=created,
        )


@dataclass
class AccuracyStats:
    """إحصائيات دقة قرارات التسعير خلال نافذة زمنية."""

    total_decisions: int
    approved_count: int
    rejected_count: int
    modified_count: int
    approval_rate: float
    avg_deviation_pct: float
    worst_brand: Optional[str]
    best_brand: Optional[str]
    window_days: int


@dataclass
class BrandFeedbackStats:
    """إحصائيات تقييمات علامة تجارية واحدة."""

    brand: str
    total: int
    approved: int
    rejected: int
    modified: int
    approval_rate: float
    avg_deviation: float


# ---------------------------------------------------------------------------
# الخدمة الرئيسية
# ---------------------------------------------------------------------------

class FeedbackCollector:
    """جامع تقييمات المستخدم على قرارات التسعير بالذكاء الاصطناعي.

    يسجّل كل تفاعل (موافقة / رفض / تعديل) ويحسب إحصائيات الدقة
    لكل علامة تجارية وشريحة سعرية ونافذة زمنية.
    """

    _CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS feedback_records (
        record_id         TEXT PRIMARY KEY,
        decision_id       INTEGER NOT NULL,
        product_name      TEXT NOT NULL,
        brand             TEXT,
        section           TEXT NOT NULL,
        ai_suggested_price REAL NOT NULL,
        ai_confidence     REAL NOT NULL,
        user_action       TEXT NOT NULL,
        user_final_price  REAL DEFAULT 0.0,
        deviation_pct     REAL DEFAULT 0.0,
        price_range_tier  TEXT NOT NULL,
        created_at        TEXT NOT NULL
    )
    """

    _CREATE_INDEXES_SQL = [
        "CREATE INDEX IF NOT EXISTS idx_fb_brand   ON feedback_records(brand)",
        "CREATE INDEX IF NOT EXISTS idx_fb_action  ON feedback_records(user_action)",
        "CREATE INDEX IF NOT EXISTS idx_fb_created ON feedback_records(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_fb_tier    ON feedback_records(price_range_tier)",
    ]

    def __init__(self, db: DatabaseManager) -> None:
        """يهيّئ الخدمة مع مدير قاعدة البيانات.

        Args:
            db: مدير قاعدة البيانات (حقن تبعية).
        """
        self._db = db

    # ------------------------------------------------------------------
    # إنشاء الجدول
    # ------------------------------------------------------------------

    def ensure_table(self) -> None:
        """ينشئ جدول feedback_records والفهارس إن لم يكونا موجودين."""
        self._db.execute(self._CREATE_TABLE_SQL)
        for idx_sql in self._CREATE_INDEXES_SQL:
            self._db.execute(idx_sql)

    # ------------------------------------------------------------------
    # تسجيل التقييم
    # ------------------------------------------------------------------

    def record(
        self,
        decision_id: int,
        product_name: str,
        section: str,
        ai_price: float,
        ai_confidence: float,
        action: str,
        *,
        user_price: float = 0.0,
        brand: Optional[str] = None,
    ) -> FeedbackRecord:
        """يسجّل تقييم المستخدم على قرار تسعير.

        Args:
            decision_id: معرّف القرار في جدول ai_decisions_log.
            product_name: اسم المنتج.
            section: القسم (price_raise / price_lower / approved ...).
            ai_price: السعر المقترح من الذكاء الاصطناعي.
            ai_confidence: درجة ثقة الذكاء الاصطناعي (0-1).
            action: إجراء المستخدم (approved / rejected / modified).
            user_price: السعر النهائي الذي اختاره المستخدم.
            brand: العلامة التجارية (اختيارية).

        Returns:
            سجل التقييم المُنشأ.
        """
        record_id = uuid.uuid4().hex[:12]
        deviation = compute_deviation(ai_price, user_price)
        tier = compute_price_tier(ai_price)
        from core.product_entity import _utcnow
        now = _utcnow()

        fb = FeedbackRecord(
            record_id=record_id,
            decision_id=decision_id,
            product_name=product_name,
            brand=brand,
            section=section,
            ai_suggested_price=ai_price,
            ai_confidence=ai_confidence,
            user_action=action,
            user_final_price=user_price,
            deviation_pct=round(deviation, 2),
            price_range_tier=tier,
            created_at=now,
        )

        self._db.execute(
            """
            INSERT INTO feedback_records
                (record_id, decision_id, product_name, brand, section,
                 ai_suggested_price, ai_confidence, user_action,
                 user_final_price, deviation_pct, price_range_tier, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fb.record_id, fb.decision_id, fb.product_name, fb.brand,
                fb.section, fb.ai_suggested_price, fb.ai_confidence,
                fb.user_action, fb.user_final_price, fb.deviation_pct,
                fb.price_range_tier, fb.created_at.isoformat(),
            ),
        )

        return fb

    # ------------------------------------------------------------------
    # إحصائيات الدقة
    # ------------------------------------------------------------------

    def get_accuracy_stats(self, *, window_days: int = 30) -> AccuracyStats:
        """يحسب إحصائيات دقة التسعير خلال نافذة زمنية.

        Args:
            window_days: عدد الأيام للنافذة الزمنية (افتراضي 30).

        Returns:
            إحصائيات الدقة الشاملة.
        """
        from core.product_entity import _utcnow
        cutoff = (_utcnow() - timedelta(days=window_days)).isoformat()

        rows = self._db.query(
            "SELECT user_action, deviation_pct, brand FROM feedback_records "
            "WHERE created_at >= ?",
            (cutoff,),
        )

        total = len(rows)
        if total == 0:
            return AccuracyStats(
                total_decisions=0, approved_count=0, rejected_count=0,
                modified_count=0, approval_rate=0.0, avg_deviation_pct=0.0,
                worst_brand=None, best_brand=None, window_days=window_days,
            )

        approved = sum(1 for r in rows if r["user_action"] == "approved")
        rejected = sum(1 for r in rows if r["user_action"] == "rejected")
        modified = sum(1 for r in rows if r["user_action"] == "modified")

        # متوسط الانحراف للتعديلات فقط
        mod_devs = [r["deviation_pct"] for r in rows if r["user_action"] == "modified"]
        avg_dev = sum(mod_devs) / len(mod_devs) if mod_devs else 0.0

        # أفضل/أسوأ علامة تجارية
        brand_counts: dict[str, dict[str, int]] = {}
        for r in rows:
            b = r["brand"]
            if b is None:
                continue
            if b not in brand_counts:
                brand_counts[b] = {"total": 0, "approved": 0}
            brand_counts[b]["total"] += 1
            if r["user_action"] == "approved":
                brand_counts[b]["approved"] += 1

        best_brand: Optional[str] = None
        worst_brand: Optional[str] = None
        if brand_counts:
            best_brand = max(
                brand_counts,
                key=lambda b: brand_counts[b]["approved"] / brand_counts[b]["total"],
            )
            worst_brand = min(
                brand_counts,
                key=lambda b: brand_counts[b]["approved"] / brand_counts[b]["total"],
            )

        return AccuracyStats(
            total_decisions=total,
            approved_count=approved,
            rejected_count=rejected,
            modified_count=modified,
            approval_rate=round(approved / total * 100, 2),
            avg_deviation_pct=round(avg_dev, 2),
            worst_brand=worst_brand,
            best_brand=best_brand,
            window_days=window_days,
        )

    # ------------------------------------------------------------------
    # إحصائيات العلامات التجارية
    # ------------------------------------------------------------------

    def get_brand_stats(self, brand: str) -> BrandFeedbackStats:
        """يحسب إحصائيات تقييمات علامة تجارية محددة.

        Args:
            brand: اسم العلامة التجارية.

        Returns:
            إحصائيات العلامة التجارية.
        """
        rows = self._db.query(
            "SELECT user_action, deviation_pct FROM feedback_records WHERE brand = ?",
            (brand,),
        )

        total = len(rows)
        if total == 0:
            return BrandFeedbackStats(
                brand=brand, total=0, approved=0, rejected=0,
                modified=0, approval_rate=0.0, avg_deviation=0.0,
            )

        approved = sum(1 for r in rows if r["user_action"] == "approved")
        rejected = sum(1 for r in rows if r["user_action"] == "rejected")
        modified = sum(1 for r in rows if r["user_action"] == "modified")
        mod_devs = [r["deviation_pct"] for r in rows if r["user_action"] == "modified"]
        avg_dev = sum(mod_devs) / len(mod_devs) if mod_devs else 0.0

        return BrandFeedbackStats(
            brand=brand,
            total=total,
            approved=approved,
            rejected=rejected,
            modified=modified,
            approval_rate=round(approved / total * 100, 2),
            avg_deviation=round(avg_dev, 2),
        )

    def get_all_brand_stats(self, *, min_decisions: int = 3) -> list[BrandFeedbackStats]:
        """يحسب إحصائيات كل العلامات التجارية التي تجاوزت الحد الأدنى من القرارات.

        Args:
            min_decisions: الحد الأدنى لعدد القرارات لتضمين العلامة (افتراضي 3).

        Returns:
            قائمة إحصائيات مرتبة حسب معدل الموافقة تنازلياً.
        """
        rows = self._db.query(
            "SELECT DISTINCT brand FROM feedback_records WHERE brand IS NOT NULL",
        )
        brands = [r["brand"] for r in rows]

        results: list[BrandFeedbackStats] = []
        for brand in brands:
            stats = self.get_brand_stats(brand)
            if stats.total >= min_decisions:
                results.append(stats)

        results.sort(key=lambda s: s.approval_rate, reverse=True)
        return results

    # ------------------------------------------------------------------
    # استعلامات
    # ------------------------------------------------------------------

    def get_recent(self, *, limit: int = 50) -> list[FeedbackRecord]:
        """يعيد أحدث التقييمات مرتبة بالتاريخ تنازلياً.

        Args:
            limit: الحد الأقصى لعدد السجلات (افتراضي 50).

        Returns:
            قائمة سجلات التقييم.
        """
        rows = self._db.query(
            "SELECT * FROM feedback_records ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [self._row_to_record(r) for r in rows]

    def get_by_section(self, section: str) -> list[FeedbackRecord]:
        """يعيد تقييمات قسم محدد.

        Args:
            section: اسم القسم (مثلاً price_raise).

        Returns:
            قائمة سجلات التقييم في القسم.
        """
        rows = self._db.query(
            "SELECT * FROM feedback_records WHERE section = ? ORDER BY created_at DESC",
            (section,),
        )
        return [self._row_to_record(r) for r in rows]

    def get_approval_rate_by_tier(self) -> dict[str, float]:
        """يحسب معدل الموافقة لكل شريحة سعرية.

        Returns:
            قاموس {شريحة: معدل_الموافقة_المئوي}.
        """
        rows = self._db.query(
            "SELECT price_range_tier, user_action FROM feedback_records",
        )

        tier_counts: dict[str, dict[str, int]] = {}
        for r in rows:
            tier = r["price_range_tier"]
            if tier not in tier_counts:
                tier_counts[tier] = {"total": 0, "approved": 0}
            tier_counts[tier]["total"] += 1
            if r["user_action"] == "approved":
                tier_counts[tier]["approved"] += 1

        result: dict[str, float] = {}
        for tier, counts in tier_counts.items():
            if counts["total"] > 0:
                result[tier] = round(counts["approved"] / counts["total"] * 100, 2)
            else:
                result[tier] = 0.0

        return result

    # ------------------------------------------------------------------
    # مساعد داخلي
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_record(row) -> FeedbackRecord:
        """يحوّل صف قاعدة البيانات إلى FeedbackRecord."""
        return FeedbackRecord(
            record_id=row["record_id"],
            decision_id=row["decision_id"],
            product_name=row["product_name"],
            brand=row["brand"],
            section=row["section"],
            ai_suggested_price=row["ai_suggested_price"],
            ai_confidence=row["ai_confidence"],
            user_action=row["user_action"],
            user_final_price=row["user_final_price"],
            deviation_pct=row["deviation_pct"],
            price_range_tier=row["price_range_tier"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
