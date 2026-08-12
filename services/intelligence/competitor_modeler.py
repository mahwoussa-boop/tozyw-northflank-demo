"""services/intelligence/competitor_modeler.py — نمذجة سلوك المنافسين التسعيري.

يحلّل أسعار المنافسين ويبني ملفاً تعريفياً لكل منافس يتضمن:
- تصنيف الاستراتيجية (عدوانية | معتدلة | متميزة | متقلبة)
- نسبة الفرق المتوسطة عن سعرنا
- درجة ثبات التسعير
- التنبؤ بردة فعل المنافس تجاه تغييرات أسعارنا

⚠️ لا يستورد Streamlit أبداً. يعتمد على DatabaseManager للتخزين.
"""
from __future__ import annotations

from core.product_entity import _utcnow

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from infrastructure.db_manager import DatabaseManager


# ════════════════════════════════════════════════════════════════════
#  نماذج البيانات
# ════════════════════════════════════════════════════════════════════

@dataclass
class CompetitorProfile:
    """ملف تعريفي لمنافس واحد يلخّص سلوكه التسعيري.

    Attributes:
        store_name: اسم المتجر المنافس.
        pricing_strategy: استراتيجية التسعير (aggressive | moderate | premium | erratic).
        avg_price_delta_pct: متوسط نسبة الفرق عن سعرنا (سالب = أرخص).
        price_count: عدد مقارنات الأسعار المستخدمة.
        min_price_ratio: أقل نسبة سعرهم/سعرنا.
        max_price_ratio: أعلى نسبة سعرهم/سعرنا.
        consistency_score: درجة ثبات التسعير (0-1، الأعلى = أكثر ثباتاً).
        last_updated: تاريخ آخر تحديث.
    """

    store_name: str = ""
    pricing_strategy: str = "moderate"
    avg_price_delta_pct: float = 0.0
    price_count: int = 0
    min_price_ratio: float = 1.0
    max_price_ratio: float = 1.0
    consistency_score: float = 1.0
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """يحوّل الملف التعريفي إلى قاموس قابل للتسلسل (JSON-safe)."""
        return {
            "store_name": self.store_name,
            "pricing_strategy": self.pricing_strategy,
            "avg_price_delta_pct": self.avg_price_delta_pct,
            "price_count": self.price_count,
            "min_price_ratio": self.min_price_ratio,
            "max_price_ratio": self.max_price_ratio,
            "consistency_score": self.consistency_score,
            "last_updated": self.last_updated.isoformat(),
        }


@dataclass
class CompetitorPrediction:
    """تنبؤ بردة فعل منافس تجاه تغيير سعري.

    Attributes:
        store_name: اسم المتجر المنافس.
        predicted_response: الاستجابة المتوقعة (match | undercut | ignore | premium).
        confidence: درجة الثقة في التنبؤ (0-1).
        expected_delta_pct: نسبة الفرق المتوقعة في سعر المنافس.
        reasoning: تفسير التنبؤ بالعربية.
    """

    store_name: str = ""
    predicted_response: str = "ignore"
    confidence: float = 0.5
    expected_delta_pct: float = 0.0
    reasoning: str = ""


# ════════════════════════════════════════════════════════════════════
#  SQL
# ════════════════════════════════════════════════════════════════════

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS competitor_profiles (
    store_name          TEXT PRIMARY KEY,
    pricing_strategy    TEXT    NOT NULL,
    avg_price_delta_pct REAL    NOT NULL,
    price_count         INTEGER NOT NULL,
    min_price_ratio     REAL    NOT NULL,
    max_price_ratio     REAL    NOT NULL,
    consistency_score   REAL    NOT NULL,
    last_updated        TEXT    NOT NULL
)
"""

_UPSERT_SQL = """
INSERT INTO competitor_profiles
    (store_name, pricing_strategy, avg_price_delta_pct, price_count,
     min_price_ratio, max_price_ratio, consistency_score, last_updated)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(store_name) DO UPDATE SET
    pricing_strategy    = excluded.pricing_strategy,
    avg_price_delta_pct = excluded.avg_price_delta_pct,
    price_count         = excluded.price_count,
    min_price_ratio     = excluded.min_price_ratio,
    max_price_ratio     = excluded.max_price_ratio,
    consistency_score   = excluded.consistency_score,
    last_updated        = excluded.last_updated
"""

_SELECT_ONE_SQL = "SELECT * FROM competitor_profiles WHERE store_name = ?"
_SELECT_ALL_SQL = "SELECT * FROM competitor_profiles ORDER BY store_name"


# ════════════════════════════════════════════════════════════════════
#  الخدمة الأساسية
# ════════════════════════════════════════════════════════════════════

class CompetitorBehaviorModeler:
    """نمذجة سلوك المنافسين التسعيري والتنبؤ بردود أفعالهم.

    تستقبل أزواج أسعار (سعرنا، سعر المنافس) وتبني ملفاً تعريفياً
    يتضمن الاستراتيجية ودرجة الثبات ومقاييس إحصائية أخرى، ثم
    تستخدم هذه الملفات للتنبؤ بكيف سيتصرف المنافس عند تغيير أسعارنا.

    Args:
        db: مدير قاعدة البيانات (DatabaseManager).
    """

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db

    # ── إعداد الجدول ──────────────────────────────────────────────

    def ensure_table(self) -> None:
        """ينشئ جدول competitor_profiles إن لم يكن موجوداً."""
        self._db.execute(_CREATE_TABLE_SQL)

    # ── بناء الملف التعريفي ────────────────────────────────────────

    def build_profile(
        self,
        store_name: str,
        price_pairs: list[tuple[float, float]],
    ) -> CompetitorProfile:
        """يبني ملفاً تعريفياً لمنافس من أزواج الأسعار ويخزّنه في قاعدة البيانات.

        Args:
            store_name: اسم المتجر المنافس.
            price_pairs: قائمة أزواج (سعرنا، سعر المنافس).

        Returns:
            CompetitorProfile: الملف التعريفي المحسوب.

        Raises:
            ValueError: إذا كانت القائمة فارغة أو تحتوي أسعاراً غير موجبة.
        """
        if not price_pairs:
            raise ValueError("قائمة أزواج الأسعار فارغة")

        ratios: list[float] = []
        for our_price, their_price in price_pairs:
            if our_price <= 0:
                raise ValueError(f"سعرنا يجب أن يكون موجباً، وُجد: {our_price}")
            ratios.append(their_price / our_price)

        # حساب متوسط النسبة والفرق المئوي
        mean_ratio = sum(ratios) / len(ratios)
        avg_delta_pct = (mean_ratio - 1.0) * 100.0

        # حساب معامل التباين (CV) لقياس الثبات
        if len(ratios) >= 2:
            variance = sum((r - mean_ratio) ** 2 for r in ratios) / len(ratios)
            std_dev = math.sqrt(variance)
            cv = std_dev / abs(mean_ratio) if mean_ratio != 0 else 0.0
        else:
            cv = 0.0

        consistency = max(0.0, min(1.0, 1.0 - cv))

        # تصنيف الاستراتيجية
        strategy = self._classify_strategy(avg_delta_pct, cv)

        min_ratio = min(ratios)
        max_ratio = max(ratios)
        now = _utcnow()

        profile = CompetitorProfile(
            store_name=store_name,
            pricing_strategy=strategy,
            avg_price_delta_pct=round(avg_delta_pct, 4),
            price_count=len(price_pairs),
            min_price_ratio=round(min_ratio, 4),
            max_price_ratio=round(max_ratio, 4),
            consistency_score=round(consistency, 4),
            last_updated=now,
        )

        # حفظ في قاعدة البيانات
        self._db.execute(
            _UPSERT_SQL,
            (
                profile.store_name,
                profile.pricing_strategy,
                profile.avg_price_delta_pct,
                profile.price_count,
                profile.min_price_ratio,
                profile.max_price_ratio,
                profile.consistency_score,
                profile.last_updated.isoformat(),
            ),
        )

        return profile

    def build_profile_from_dataframe(
        self,
        df: Any,
        *,
        our_price_col: str = "سعرنا",
        their_price_col: str = "سعر المنافس",
        store_col: str = "المنافس",
    ) -> list[CompetitorProfile]:
        """يبني ملفات تعريفية لعدة منافسين من DataFrame.

        Args:
            df: DataFrame يحتوي أعمدة الأسعار واسم المنافس.
            our_price_col: اسم عمود سعرنا.
            their_price_col: اسم عمود سعر المنافس.
            store_col: اسم عمود اسم المنافس.

        Returns:
            قائمة ملفات تعريفية لكل منافس فريد.
        """
        profiles: list[CompetitorProfile] = []

        # تجميع الأسعار حسب المنافس
        store_pairs: dict[str, list[tuple[float, float]]] = {}
        # ⚡ PERFORMANCE FIX: استبدال iterrows البطيء بـ to_dict('records')
        for row in df.to_dict('records'):
            store = str(row[store_col])
            our_price = float(row[our_price_col])
            their_price = float(row[their_price_col])
            store_pairs.setdefault(store, []).append((our_price, their_price))

        for store, pairs in store_pairs.items():
            profiles.append(self.build_profile(store, pairs))

        return profiles

    # ── التنبؤ ──────────────────────────────────────────────────────

    def predict_response(
        self,
        store_name: str,
        our_price_change_pct: float,
    ) -> CompetitorPrediction:
        """يتنبأ بردة فعل المنافس تجاه تغيير مخطط في أسعارنا.

        Args:
            store_name: اسم المتجر المنافس.
            our_price_change_pct: نسبة التغيير المخطط في سعرنا (%).

        Returns:
            CompetitorPrediction: التنبؤ بردة الفعل مع درجة الثقة.

        Raises:
            ValueError: إذا لم يوجد ملف تعريفي للمنافس.
        """
        profile = self.get_profile(store_name)
        if profile is None:
            raise ValueError(f"لا يوجد ملف تعريفي للمنافس: {store_name}")

        strategy = profile.pricing_strategy

        if strategy == "aggressive":
            return self._predict_aggressive(profile, our_price_change_pct)
        elif strategy == "premium":
            return self._predict_premium(profile, our_price_change_pct)
        elif strategy == "erratic":
            return self._predict_erratic(profile, our_price_change_pct)
        else:  # moderate
            return self._predict_moderate(profile, our_price_change_pct)

    # ── استعلامات قاعدة البيانات ───────────────────────────────────

    def get_profile(self, store_name: str) -> Optional[CompetitorProfile]:
        """يسترجع الملف التعريفي لمنافس من قاعدة البيانات.

        Args:
            store_name: اسم المتجر المنافس.

        Returns:
            CompetitorProfile أو None إذا لم يوجد.
        """
        row = self._db.query_one(_SELECT_ONE_SQL, (store_name,))
        if row is None:
            return None
        return self._row_to_profile(row)

    def get_all_profiles(self) -> list[CompetitorProfile]:
        """يسترجع جميع الملفات التعريفية المخزّنة.

        Returns:
            قائمة بجميع ملفات المنافسين التعريفية.
        """
        rows = self._db.query(_SELECT_ALL_SQL)
        return [self._row_to_profile(r) for r in rows]

    def rank_competitors(self) -> list[CompetitorProfile]:
        """يرتّب المنافسين حسب مستوى التهديد (الأكثر عدوانية أولاً).

        ترتيب التهديد:
        1. المنافس العدواني (aggressive) — الأكثر خطورة
        2. المنافس المتقلب (erratic) — يصعب التنبؤ به
        3. المنافس المعتدل (moderate)
        4. المنافس المتميز (premium) — الأقل تهديداً

        ضمن نفس الفئة يُرتَّب حسب avg_price_delta_pct تصاعدياً (الأرخص أولاً).

        Returns:
            قائمة مرتبة حسب مستوى التهديد.
        """
        profiles = self.get_all_profiles()
        threat_order = {
            "aggressive": 0,
            "erratic": 1,
            "moderate": 2,
            "premium": 3,
        }
        profiles.sort(
            key=lambda p: (threat_order.get(p.pricing_strategy, 99), p.avg_price_delta_pct),
        )
        return profiles

    # ── مساعدات داخلية ──────────────────────────────────────────────

    @staticmethod
    def _classify_strategy(avg_delta_pct: float, cv: float) -> str:
        """يصنّف استراتيجية المنافس بناءً على متوسط الفرق ومعامل التباين.

        Args:
            avg_delta_pct: متوسط الفرق المئوي.
            cv: معامل التباين.

        Returns:
            تصنيف الاستراتيجية كنص.
        """
        if cv > 0.3:
            return "erratic"
        if avg_delta_pct < -10.0:
            return "aggressive"
        if avg_delta_pct > 10.0:
            return "premium"
        return "moderate"

    @staticmethod
    def _row_to_profile(row: Any) -> CompetitorProfile:
        """يحوّل صف قاعدة بيانات إلى CompetitorProfile."""
        return CompetitorProfile(
            store_name=row["store_name"],
            pricing_strategy=row["pricing_strategy"],
            avg_price_delta_pct=row["avg_price_delta_pct"],
            price_count=row["price_count"],
            min_price_ratio=row["min_price_ratio"],
            max_price_ratio=row["max_price_ratio"],
            consistency_score=row["consistency_score"],
            last_updated=datetime.fromisoformat(row["last_updated"]),
        )

    @staticmethod
    def _predict_aggressive(
        profile: CompetitorProfile,
        our_change_pct: float,
    ) -> CompetitorPrediction:
        """تنبؤ لمنافس عدواني: يميل لمطابقة أو تقويض أسعارنا."""
        if our_change_pct < 0:
            # خفّضنا السعر → المنافس العدواني سيحاول التقويض
            return CompetitorPrediction(
                store_name=profile.store_name,
                predicted_response="undercut",
                confidence=0.85 * profile.consistency_score,
                expected_delta_pct=our_change_pct * 1.2,
                reasoning="المنافس عدواني ويميل لتقويض أسعارنا عند تخفيضها",
            )
        else:
            # رفعنا السعر → المنافس العدواني سيطابق جزئياً
            return CompetitorPrediction(
                store_name=profile.store_name,
                predicted_response="match",
                confidence=0.7 * profile.consistency_score,
                expected_delta_pct=our_change_pct * 0.5,
                reasoning="المنافس عدواني وقد يرفع سعره جزئياً لمطابقة الزيادة",
            )

    @staticmethod
    def _predict_premium(
        profile: CompetitorProfile,
        our_change_pct: float,
    ) -> CompetitorPrediction:
        """تنبؤ لمنافس متميز: غالباً يتجاهل تغييرات أسعارنا."""
        return CompetitorPrediction(
            store_name=profile.store_name,
            predicted_response="ignore",
            confidence=0.8 * profile.consistency_score,
            expected_delta_pct=0.0,
            reasoning="المنافس يتّبع استراتيجية تسعير متميزة ولا يتأثر عادةً بتغييرات أسعارنا",
        )

    @staticmethod
    def _predict_erratic(
        profile: CompetitorProfile,
        our_change_pct: float,
    ) -> CompetitorPrediction:
        """تنبؤ لمنافس متقلب: ثقة منخفضة في أي تنبؤ."""
        return CompetitorPrediction(
            store_name=profile.store_name,
            predicted_response="match",
            confidence=0.3,
            expected_delta_pct=our_change_pct * 0.5,
            reasoning="المنافس متقلب التسعير ويصعب التنبؤ بسلوكه بثقة عالية",
        )

    @staticmethod
    def _predict_moderate(
        profile: CompetitorProfile,
        our_change_pct: float,
    ) -> CompetitorPrediction:
        """تنبؤ لمنافس معتدل: يستجيب بشكل متناسب."""
        return CompetitorPrediction(
            store_name=profile.store_name,
            predicted_response="match",
            confidence=0.65 * profile.consistency_score,
            expected_delta_pct=our_change_pct * 0.7,
            reasoning="المنافس معتدل ويميل للاستجابة بشكل متناسب لتغييرات أسعارنا",
        )
