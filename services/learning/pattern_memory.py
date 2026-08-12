"""services/learning/pattern_memory.py — ذاكرة أنماط قرارات المستخدم للتعلّم الذكي.

تكتشف وتحفظ أنماط سلوك المستخدم في قرارات التسعير لتمكين الذكاء الاصطناعي
من التعلّم المستمر والتكيّف مع تفضيلات المستخدم:

أمثلة على الأنماط:
- "المستخدم يرفض دائماً خفض أسعار العطور الفاخرة أكثر من 10%"
- "المستخدم يوافق على أسعار لطافة بدون تعديل"
- "المستخدم يضيف دائماً 5% فوق اقتراح الذكاء الاصطناعي للمنتجات الحصرية"

⚠️ لا يستورد streamlit — خدمة خالصة قابلة للحقن.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from infrastructure.db_manager import DatabaseManager
from services.learning.feedback_collector import FeedbackCollector


# ---------------------------------------------------------------------------
# نموذج البيانات
# ---------------------------------------------------------------------------

@dataclass
class UserPattern:
    """نمط سلوكي مُكتشَف من قرارات المستخدم.

    Attributes:
        pattern_id: معرّف فريد (أول 12 حرف من UUID).
        pattern_type: نوع النمط (brand_preference | price_sensitivity | section_bias | tier_behavior).
        description: وصف عربي مقروء للنمط.
        condition: شرط منظّم (مثلاً {"brand": "Dior", "direction": "lower", "max_drop_pct": 10}).
        confidence: مستوى الثقة في النمط (0-1).
        sample_count: عدد القرارات الداعمة لهذا النمط.
        approval_rate: معدل الموافقة للمنتجات المطابقة.
        avg_deviation: متوسط انحراف السعر للمنتجات المطابقة.
        created_at: تاريخ اكتشاف النمط.
        last_seen: تاريخ آخر مشاهدة للنمط.
    """

    pattern_id: str
    pattern_type: str
    description: str
    condition: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    sample_count: int = 0
    approval_rate: float = 0.0
    avg_deviation: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        """يحوّل النمط إلى قاموس قابل للتسلسل بـ JSON."""
        return {
            "pattern_id": self.pattern_id,
            "pattern_type": self.pattern_type,
            "description": self.description,
            "condition": self.condition,
            "confidence": self.confidence,
            "sample_count": self.sample_count,
            "approval_rate": self.approval_rate,
            "avg_deviation": self.avg_deviation,
            "created_at": self.created_at.isoformat(),
            "last_seen": self.last_seen.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> UserPattern:
        """ينشئ نمطاً من قاموس (مثلاً من JSON المُخزَّن).

        Args:
            d: القاموس المصدري.

        Returns:
            كائن UserPattern.
        """
        created = d["created_at"]
        if isinstance(created, str):
            created = datetime.fromisoformat(created)
        last_seen = d["last_seen"]
        if isinstance(last_seen, str):
            last_seen = datetime.fromisoformat(last_seen)
        condition = d["condition"]
        if isinstance(condition, str):
            condition = json.loads(condition)
        return cls(
            pattern_id=d["pattern_id"],
            pattern_type=d["pattern_type"],
            description=d["description"],
            condition=condition,
            confidence=d["confidence"],
            sample_count=d["sample_count"],
            approval_rate=d.get("approval_rate", 0.0),
            avg_deviation=d.get("avg_deviation", 0.0),
            created_at=created,
            last_seen=last_seen,
        )


# ---------------------------------------------------------------------------
# الخدمة الرئيسية
# ---------------------------------------------------------------------------

class PatternMemory:
    """ذاكرة أنماط المستخدم — تكتشف وتحفظ أنماط القرارات للتعلّم الذكي.

    تحلّل بيانات التقييمات (FeedbackCollector) لاكتشاف أنماط سلوكية
    مثل تفضيلات العلامات التجارية والحساسية السعرية والانحياز القسمي،
    ثم تحفظها في قاعدة البيانات لإثراء التوجيهات المستقبلية للذكاء الاصطناعي.
    """

    _CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS user_patterns (
        pattern_id    TEXT PRIMARY KEY,
        pattern_type  TEXT NOT NULL,
        description   TEXT NOT NULL,
        condition     TEXT NOT NULL,
        confidence    REAL NOT NULL,
        sample_count  INTEGER NOT NULL,
        approval_rate REAL DEFAULT 0.0,
        avg_deviation REAL DEFAULT 0.0,
        created_at    TEXT NOT NULL,
        last_seen     TEXT NOT NULL
    )
    """

    _CREATE_INDEXES_SQL = [
        "CREATE INDEX IF NOT EXISTS idx_up_type       ON user_patterns(pattern_type)",
        "CREATE INDEX IF NOT EXISTS idx_up_confidence  ON user_patterns(confidence)",
    ]

    def __init__(self, feedback: FeedbackCollector, db: DatabaseManager) -> None:
        """يهيّئ الخدمة مع جامع التقييمات ومدير قاعدة البيانات.

        Args:
            feedback: خدمة جمع التقييمات (مصدر البيانات).
            db: مدير قاعدة البيانات (حقن تبعية).
        """
        self._feedback = feedback
        self._db = db

    # ------------------------------------------------------------------
    # إنشاء الجدول
    # ------------------------------------------------------------------

    def ensure_table(self) -> None:
        """ينشئ جدول user_patterns والفهارس إن لم يكونا موجودين."""
        self._db.execute(self._CREATE_TABLE_SQL)
        for idx_sql in self._CREATE_INDEXES_SQL:
            self._db.execute(idx_sql)

    # ------------------------------------------------------------------
    # اكتشاف الأنماط
    # ------------------------------------------------------------------

    def discover_patterns(
        self,
        *,
        min_samples: int = 5,
        min_confidence: float = 0.7,
    ) -> list[UserPattern]:
        """يحلّل بيانات التقييمات لاكتشاف أنماط سلوكية.

        يبحث عن ثلاثة أنواع من الأنماط:
        1. **brand_preference**: علامات تجارية يوافق/يرفض المستخدم عليها باستمرار.
        2. **price_sensitivity**: شرائح سعرية ذات تفضيلات قوية.
        3. **section_bias**: أقسام يتصرّف المستخدم فيها بشكل مختلف.

        Args:
            min_samples: الحد الأدنى لعدد القرارات الداعمة للنمط.
            min_confidence: الحد الأدنى لمستوى الثقة (0-1).

        Returns:
            قائمة الأنماط المُكتشَفة.
        """
        patterns: list[UserPattern] = []

        patterns.extend(self._discover_brand_patterns(min_samples, min_confidence))
        patterns.extend(self._discover_price_sensitivity_patterns(min_samples, min_confidence))
        patterns.extend(self._discover_section_bias_patterns(min_samples, min_confidence))

        # حفظ الأنماط المُكتشَفة في قاعدة البيانات
        for p in patterns:
            self._upsert_pattern(p)

        return patterns

    def _discover_brand_patterns(
        self, min_samples: int, min_confidence: float,
    ) -> list[UserPattern]:
        """يكتشف أنماط تفضيل العلامات التجارية.

        يبحث عن علامات تجارية يوافق عليها المستخدم بنسبة > 80%
        أو يرفضها بنسبة > 70% (معدل موافقة < 30%).

        Args:
            min_samples: الحد الأدنى لعدد القرارات.
            min_confidence: الحد الأدنى للثقة.

        Returns:
            قائمة أنماط تفضيل العلامات التجارية.
        """
        rows = self._db.query(
            """
            SELECT brand, user_action, deviation_pct
            FROM feedback_records
            WHERE brand IS NOT NULL AND brand != ''
            """,
        )

        if not rows:
            return []

        # تجميع حسب العلامة التجارية
        brand_data: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            brand = r["brand"]
            if brand not in brand_data:
                brand_data[brand] = []
            brand_data[brand].append({
                "action": r["user_action"],
                "deviation": r["deviation_pct"],
            })

        patterns: list[UserPattern] = []
        from core.product_entity import _utcnow
        now = _utcnow()

        for brand, decisions in brand_data.items():
            total = len(decisions)
            if total < min_samples:
                continue

            approved = sum(1 for d in decisions if d["action"] == "approved")
            approval_rate = approved / total

            # حساب متوسط الانحراف
            deviations = [d["deviation"] for d in decisions if d["deviation"] != 0.0]
            avg_dev = sum(deviations) / len(deviations) if deviations else 0.0

            # نمط موافقة عالية
            if approval_rate > 0.8:
                confidence = min(approval_rate, 1.0)
                if confidence >= min_confidence:
                    patterns.append(UserPattern(
                        pattern_id=uuid.uuid4().hex[:12],
                        pattern_type="brand_preference",
                        description=f"المستخدم يوافق دائماً على أسعار {brand} (معدل موافقة {approval_rate * 100:.0f}%)",
                        condition={"brand": brand, "preference": "approve", "approval_rate": round(approval_rate, 2)},
                        confidence=round(confidence, 2),
                        sample_count=total,
                        approval_rate=round(approval_rate, 2),
                        avg_deviation=round(avg_dev, 2),
                        created_at=now,
                        last_seen=now,
                    ))

            # نمط رفض عالي
            elif approval_rate < 0.3:
                confidence = min(1.0 - approval_rate, 1.0)
                if confidence >= min_confidence:
                    patterns.append(UserPattern(
                        pattern_id=uuid.uuid4().hex[:12],
                        pattern_type="brand_preference",
                        description=f"المستخدم يرفض دائماً أسعار {brand} (معدل رفض {(1 - approval_rate) * 100:.0f}%)",
                        condition={"brand": brand, "preference": "reject", "approval_rate": round(approval_rate, 2)},
                        confidence=round(confidence, 2),
                        sample_count=total,
                        approval_rate=round(approval_rate, 2),
                        avg_deviation=round(avg_dev, 2),
                        created_at=now,
                        last_seen=now,
                    ))

        return patterns

    def _discover_price_sensitivity_patterns(
        self, min_samples: int, min_confidence: float,
    ) -> list[UserPattern]:
        """يكتشف أنماط الحساسية السعرية حسب الشرائح.

        يبحث عن شرائح سعرية ذات معدل موافقة مرتفع (> 80%) أو منخفض (< 30%).

        Args:
            min_samples: الحد الأدنى لعدد القرارات.
            min_confidence: الحد الأدنى للثقة.

        Returns:
            قائمة أنماط الحساسية السعرية.
        """
        rows = self._db.query(
            """
            SELECT price_range_tier, user_action, deviation_pct
            FROM feedback_records
            """,
        )

        if not rows:
            return []

        # تجميع حسب الشريحة السعرية
        tier_data: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            tier = r["price_range_tier"]
            if tier not in tier_data:
                tier_data[tier] = []
            tier_data[tier].append({
                "action": r["user_action"],
                "deviation": r["deviation_pct"],
            })

        _TIER_LABELS = {
            "budget": "الاقتصادية",
            "mid": "المتوسطة",
            "premium": "العالية",
            "luxury": "الفاخرة",
        }

        patterns: list[UserPattern] = []
        from core.product_entity import _utcnow
        now = _utcnow()

        for tier, decisions in tier_data.items():
            total = len(decisions)
            if total < min_samples:
                continue

            approved = sum(1 for d in decisions if d["action"] == "approved")
            approval_rate = approved / total

            deviations = [d["deviation"] for d in decisions if d["deviation"] != 0.0]
            avg_dev = sum(deviations) / len(deviations) if deviations else 0.0

            tier_label = _TIER_LABELS.get(tier, tier)

            if approval_rate > 0.8:
                confidence = min(approval_rate, 1.0)
                if confidence >= min_confidence:
                    patterns.append(UserPattern(
                        pattern_id=uuid.uuid4().hex[:12],
                        pattern_type="price_sensitivity",
                        description=f"المستخدم يوافق على أسعار الشريحة {tier_label} بثقة عالية ({approval_rate * 100:.0f}%)",
                        condition={"price_tier": tier, "preference": "approve", "approval_rate": round(approval_rate, 2)},
                        confidence=round(confidence, 2),
                        sample_count=total,
                        approval_rate=round(approval_rate, 2),
                        avg_deviation=round(avg_dev, 2),
                        created_at=now,
                        last_seen=now,
                    ))
            elif approval_rate < 0.3:
                confidence = min(1.0 - approval_rate, 1.0)
                if confidence >= min_confidence:
                    patterns.append(UserPattern(
                        pattern_id=uuid.uuid4().hex[:12],
                        pattern_type="price_sensitivity",
                        description=f"المستخدم يرفض أسعار الشريحة {tier_label} غالباً ({(1 - approval_rate) * 100:.0f}%)",
                        condition={"price_tier": tier, "preference": "reject", "approval_rate": round(approval_rate, 2)},
                        confidence=round(confidence, 2),
                        sample_count=total,
                        approval_rate=round(approval_rate, 2),
                        avg_deviation=round(avg_dev, 2),
                        created_at=now,
                        last_seen=now,
                    ))

        return patterns

    def _discover_section_bias_patterns(
        self, min_samples: int, min_confidence: float,
    ) -> list[UserPattern]:
        """يكتشف أنماط الانحياز القسمي.

        يبحث عن أقسام يتصرّف المستخدم فيها بنمط ثابت
        (مثلاً: يوافق دائماً على رفع الأسعار لكن يرفض خفضها).

        Args:
            min_samples: الحد الأدنى لعدد القرارات.
            min_confidence: الحد الأدنى للثقة.

        Returns:
            قائمة أنماط الانحياز القسمي.
        """
        rows = self._db.query(
            """
            SELECT section, user_action, deviation_pct
            FROM feedback_records
            """,
        )

        if not rows:
            return []

        # تجميع حسب القسم
        section_data: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            section = r["section"]
            if section not in section_data:
                section_data[section] = []
            section_data[section].append({
                "action": r["user_action"],
                "deviation": r["deviation_pct"],
            })

        _SECTION_LABELS = {
            "price_raise": "رفع الأسعار",
            "price_lower": "خفض الأسعار",
            "approved": "الأسعار المعتمدة",
            "missing": "المنتجات المفقودة",
        }

        patterns: list[UserPattern] = []
        from core.product_entity import _utcnow
        now = _utcnow()

        for section, decisions in section_data.items():
            total = len(decisions)
            if total < min_samples:
                continue

            approved = sum(1 for d in decisions if d["action"] == "approved")
            approval_rate = approved / total

            deviations = [d["deviation"] for d in decisions if d["deviation"] != 0.0]
            avg_dev = sum(deviations) / len(deviations) if deviations else 0.0

            section_label = _SECTION_LABELS.get(section, section)

            if approval_rate > 0.8:
                confidence = min(approval_rate, 1.0)
                if confidence >= min_confidence:
                    patterns.append(UserPattern(
                        pattern_id=uuid.uuid4().hex[:12],
                        pattern_type="section_bias",
                        description=f"المستخدم يوافق دائماً على قسم {section_label} ({approval_rate * 100:.0f}%)",
                        condition={"section": section, "preference": "approve", "approval_rate": round(approval_rate, 2)},
                        confidence=round(confidence, 2),
                        sample_count=total,
                        approval_rate=round(approval_rate, 2),
                        avg_deviation=round(avg_dev, 2),
                        created_at=now,
                        last_seen=now,
                    ))
            elif approval_rate < 0.3:
                confidence = min(1.0 - approval_rate, 1.0)
                if confidence >= min_confidence:
                    patterns.append(UserPattern(
                        pattern_id=uuid.uuid4().hex[:12],
                        pattern_type="section_bias",
                        description=f"المستخدم يرفض دائماً قسم {section_label} ({(1 - approval_rate) * 100:.0f}%)",
                        condition={"section": section, "preference": "reject", "approval_rate": round(approval_rate, 2)},
                        confidence=round(confidence, 2),
                        sample_count=total,
                        approval_rate=round(approval_rate, 2),
                        avg_deviation=round(avg_dev, 2),
                        created_at=now,
                        last_seen=now,
                    ))

        return patterns

    # ------------------------------------------------------------------
    # حفظ/تحديث النمط
    # ------------------------------------------------------------------

    def _upsert_pattern(self, pattern: UserPattern) -> None:
        """يحفظ أو يحدّث نمطاً في قاعدة البيانات.

        Args:
            pattern: النمط المراد حفظه.
        """
        self._db.execute(
            """
            INSERT OR REPLACE INTO user_patterns
                (pattern_id, pattern_type, description, condition, confidence,
                 sample_count, approval_rate, avg_deviation, created_at, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pattern.pattern_id,
                pattern.pattern_type,
                pattern.description,
                json.dumps(pattern.condition, ensure_ascii=False),
                pattern.confidence,
                pattern.sample_count,
                pattern.approval_rate,
                pattern.avg_deviation,
                pattern.created_at.isoformat(),
                pattern.last_seen.isoformat(),
            ),
        )

    # ------------------------------------------------------------------
    # استعلام الأنماط
    # ------------------------------------------------------------------

    def get_applicable_patterns(
        self,
        *,
        brand: str = "",
        section: str = "",
        price_tier: str = "",
    ) -> list[UserPattern]:
        """يعيد الأنماط المطابقة للسياق المحدد.

        يبحث في الأنماط المخزّنة عن تلك التي تطابق العلامة التجارية
        أو القسم أو الشريحة السعرية المحددة.

        Args:
            brand: اسم العلامة التجارية (اختياري).
            section: اسم القسم (اختياري).
            price_tier: الشريحة السعرية (اختياري).

        Returns:
            قائمة الأنماط المطابقة مرتّبة بالثقة تنازلياً.
        """
        rows = self._db.query(
            "SELECT * FROM user_patterns ORDER BY confidence DESC",
        )

        patterns: list[UserPattern] = []
        for r in rows:
            condition = json.loads(r["condition"])
            matches = False

            if brand and condition.get("brand", "") == brand:
                matches = True
            if section and condition.get("section", "") == section:
                matches = True
            if price_tier and condition.get("price_tier", "") == price_tier:
                matches = True

            if matches:
                patterns.append(self._row_to_pattern(r))

        return patterns

    def get_all_patterns(self, *, limit: int = 50) -> list[UserPattern]:
        """يعيد كل الأنماط المخزّنة مرتّبة بالثقة تنازلياً.

        Args:
            limit: الحد الأقصى لعدد الأنماط (افتراضي 50).

        Returns:
            قائمة الأنماط المخزّنة.
        """
        rows = self._db.query(
            "SELECT * FROM user_patterns ORDER BY confidence DESC LIMIT ?",
            (limit,),
        )
        return [self._row_to_pattern(r) for r in rows]

    # ------------------------------------------------------------------
    # تنسيق للعرض
    # ------------------------------------------------------------------

    def format_for_prompt(self, patterns: list[UserPattern]) -> str:
        """ينسّق الأنماط كتعليمات لإثراء توجيهات الذكاء الاصطناعي.

        ينتج نصاً عربياً بنقاط (bullets) يصف كل نمط مع مستوى الثقة
        وعدد القرارات الداعمة.

        Args:
            patterns: قائمة الأنماط المراد تنسيقها.

        Returns:
            نص عربي منسّق بنقاط. سلسلة فارغة إذا لم تكن هناك أنماط.
        """
        if not patterns:
            return ""

        lines: list[str] = []
        for p in patterns:
            confidence_pct = int(p.confidence * 100)
            lines.append(
                f"- {p.description} (ثقة {confidence_pct}%، {p.sample_count} قرار)"
            )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # حذف وإحصاء
    # ------------------------------------------------------------------

    def delete_pattern(self, pattern_id: str) -> bool:
        """يحذف نمطاً من قاعدة البيانات.

        Args:
            pattern_id: معرّف النمط المراد حذفه.

        Returns:
            True إذا تم الحذف، False إذا لم يُوجَد النمط.
        """
        cursor = self._db.execute(
            "DELETE FROM user_patterns WHERE pattern_id = ?",
            (pattern_id,),
        )
        return cursor.rowcount > 0

    def count(self) -> int:
        """يعيد عدد الأنماط المخزّنة.

        Returns:
            عدد الأنماط الإجمالي.
        """
        row = self._db.query_one("SELECT COUNT(*) AS cnt FROM user_patterns")
        return row["cnt"] if row else 0

    # ------------------------------------------------------------------
    # مساعد داخلي
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_pattern(row) -> UserPattern:
        """يحوّل صف قاعدة البيانات إلى UserPattern."""
        return UserPattern(
            pattern_id=row["pattern_id"],
            pattern_type=row["pattern_type"],
            description=row["description"],
            condition=json.loads(row["condition"]),
            confidence=row["confidence"],
            sample_count=row["sample_count"],
            approval_rate=row["approval_rate"],
            avg_deviation=row["avg_deviation"],
            created_at=datetime.fromisoformat(row["created_at"]),
            last_seen=datetime.fromisoformat(row["last_seen"]),
        )
