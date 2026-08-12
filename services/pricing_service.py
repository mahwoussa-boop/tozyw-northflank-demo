"""services/pricing_service.py — خدمة التسعير الذكي المتقدمة (v2).

⚠️ محرك تجريبي — غير موصول بالإنتاج وليس دماغ التسعير. الدماغ الحيّ:
   ``services/opportunity_service`` (سياسة v1). يُمنع وصله قبل المواءمة:
   مرجع الأقل المتوفّر، حاجز ±20% من وسيط السوق، أرضية التكلفة وحدها،
   إلغاء الذروة الزمنية/تعديل المخزون/تعظيم الإيراد — وخلف موافقة المالك.
   (تدقيق 2026-07-08: يَعُدّ المتجر النافد ضمن مرجع السعر ⇒ يخالف v1.)
   (2026-07-08: فُصل من حاوية الحقن — لا يُنشأ في الإنتاج.)

تحسينات v2:
- دمج ذكي مع محركات الاستخبارات (تنبؤ الأسعار + نمذجة المنافسين + المواسم)
- تجميع متعدد المنافسين (min/max/avg/weighted) بدل منافس واحد
- حماية الهامش (أرضية/سقف) صارمة
- استراتيجيات تسعير ديناميكية حسب سلوك المنافس
- كاش للقرارات السعرية
- معالجة دفعية لمجموعات المنتجات
- مراعاة الاتجاه السعري والمواسم التجارية

#PRESERVED_LOGIC: واجهة PricingService.evaluate() متوافقة 100% مع الإصدار السابق.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional, Protocol

from conf.constants import PRICE_TOLERANCE
from core.enums import ActionType
from core.models import AIPriceDecision, PriceDiff

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════
#  نصوص القرار الحرفية (#PRESERVED_LOGIC)
# ════════════════════════════════════════════════════════════════════
DECISION_RAISE = "🔴 سعر أعلى"
DECISION_LOWER = "🟢 سعر أقل"
DECISION_APPROVED = "✅ موافق"
DECISION_NO_COMP_PRICE = "⚠️ تحت المراجعة — بلا سعر منافس"

# ════════════════════════════════════════════════════════════════════
#  معاملات العتبة الذكية (#PRESERVED_LOGIC engine.py:2339-2341)
# ════════════════════════════════════════════════════════════════════
_HIGH_AVG = 300.0
_HIGH_PCT = 0.05
_MID_AVG = 100.0
_LOW_TOL = 5.0

# ════════════════════════════════════════════════════════════════════
#  معاملات التسعير الذكي v2
# ════════════════════════════════════════════════════════════════════
_MARGIN_MIN_PCT = 15.0          # هامش ربح أدنى %
_MARGIN_MIN_ABS = 20.0          # هامش ربح أدنى مطلق (ريال)
_MAX_PRICE_FACTOR = 2.5         # سقف السعر = × أكبر مرجع
_SMART_UNDERCUT_PCT = 0.02      # تقويض ذكي: 2% تحت سعر المنافس العدواني
_MATCH_TOLERANCE = 0.01         # تسامح المطابقة السعرية 1%
_TREND_WEIGHT = 0.15            # وزن اتجاه السعر في القرار
_SEASONAL_WEIGHT = 0.10         # وزن الموسم في القرار


# ───────────────────────────────────────────────────────────────────
#  بروتوكولات الوحدات الخارجية (للحقن الكسول)
# ───────────────────────────────────────────────────────────────────

class _CompetitorModelerLike(Protocol):
    def get_profile(self, store_name: str) -> Any: ...
    def rank_competitors(self) -> list[Any]: ...


class _PricePredictorLike(Protocol):
    def predict(self, prices: list[float], *, product_name: str = "") -> Any: ...


class _SeasonalEngineLike(Protocol):
    def adjust_price_for_season(
        self, base_price: float, *, category: str = "perfumes", current_date: Optional[Any] = None,
    ) -> tuple[float, str]: ...


class _PriceMonitorLike(Protocol):
    def take_snapshot(
        self, *, product_name: str, brand: str = "", our_price: float, competitor_prices: dict[str, float],
    ) -> Any: ...


# ───────────────────────────────────────────────────────────────────
#  كاش القرارات السعرية
# ───────────────────────────────────────────────────────────────────

class PricingCache:
    """كاش بسيط للقرارات السعرية (مفتاح = product_id + hash الأسعار)."""

    def __init__(self, ttl_sec: int = 300) -> None:
        self._store: dict[str, tuple[Any, datetime]] = {}
        self._ttl = timedelta(seconds=ttl_sec)

    def _key(self, product_id: str, our_price: float, comp_prices: dict[str, float]) -> str:
        payload = f"{product_id}:{our_price}:{json.dumps(comp_prices, sort_keys=True, ensure_ascii=False)}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def get(self, product_id: str, our_price: float, comp_prices: dict[str, float]) -> Optional[tuple[PriceDecision, PriceDiff]]:
        key = self._key(product_id, our_price, comp_prices)
        entry = self._store.get(key)
        if entry is None:
            return None
        result, ts = entry
        if datetime.now(timezone.utc) - ts > self._ttl:
            del self._store[key]
            return None
        return result

    def set(self, product_id: str, our_price: float, comp_prices: dict[str, float], value: tuple[PriceDecision, PriceDiff]) -> None:
        key = self._key(product_id, our_price, comp_prices)
        self._store[key] = (value, datetime.now(timezone.utc))

    def clear(self) -> None:
        self._store.clear()

    def size(self) -> int:
        return len(self._store)


# ════════════════════════════════════════════════════════════════════
#  هياكل البيانات
# ════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class PriceDecision:
    """قرار سعري: النص + الفرق + العتبة المستخدمة + الإجراء."""

    decision: str
    diff: float
    threshold: float
    action: Optional[ActionType]


@dataclass
class CompetitorPriceInfo:
    """معلومات تجميعية لأسعار المنافسين."""

    prices: dict[str, float] = field(default_factory=dict)
    min_price: float = 0.0
    max_price: float = 0.0
    avg_price: float = 0.0
    median_price: float = 0.0
    count: int = 0
    cheapest_store: str = ""
    most_expensive_store: str = ""

    @classmethod
    def from_dict(cls, prices: dict[str, float]) -> "CompetitorPriceInfo":
        """يبني معلومات تجميعية من قاموس أسعار."""
        valid = {k: v for k, v in prices.items() if v > 0}
        if not valid:
            return cls()
        vals = list(valid.values())
        min_v = min(vals)
        max_v = max(vals)
        avg_v = sum(vals) / len(vals)
        s = sorted(vals)
        n = len(s)
        mid = n // 2
        median_v = (s[mid - 1] + s[mid]) / 2 if n % 2 == 0 else s[mid]
        cheapest = min(valid, key=valid.get, default="")  # type: ignore[arg-type]
        expensive = max(valid, key=valid.get, default="")  # type: ignore[arg-type]
        return cls(
            prices=valid,
            min_price=min_v,
            max_price=max_v,
            avg_price=avg_v,
            median_price=median_v,
            count=len(valid),
            cheapest_store=cheapest,
            most_expensive_store=expensive,
        )


@dataclass
class PricingContext:
    """سياق تسعير غني لمحرك القرار الذكي."""

    product_id: str = ""
    product_name: str = ""
    our_price: float = 0.0
    cost_price: float = 0.0
    brand: str = ""
    category: str = "perfumes"
    competitor_info: CompetitorPriceInfo = field(default_factory=CompetitorPriceInfo)
    # بيانات استخباراتية (اختيارية)
    trend_direction: str = "stable"      # rising | falling | stable | volatile
    trend_slope_pct: float = 0.0         # نسبة التغير الأسبوعية
    competitor_strategy: str = "moderate"  # aggressive | moderate | premium | erratic
    season_adjustment_pct: float = 0.0   # تعديل موسمي %
    season_reason: str = ""


# ════════════════════════════════════════════════════════════════════
#  الدوال الأساسية (محفوظة من الإصدار السابق)
# ════════════════════════════════════════════════════════════════════

def smart_price_threshold(
    our_price: float, comp_price: float, default_tol: float = PRICE_TOLERANCE,
) -> float:
    """عتبة تسامح «✅ موافق» ديناميكية حسب متوسط السعر."""
    tol = default_tol if default_tol is not None else 10
    if our_price <= 0 or comp_price <= 0:
        return float(tol)
    avg = (our_price + comp_price) / 2
    if avg >= _HIGH_AVG:
        return avg * _HIGH_PCT
    if avg >= _MID_AVG:
        return float(tol)
    return _LOW_TOL


def decide_price(
    our_price: float, comp_price: float, default_tol: float = PRICE_TOLERANCE,
) -> PriceDecision:
    """يقرّر القسم السعري. diff = سعرنا − سعر المنافس (موجب = نحن أعلى)."""
    if our_price > 0 and comp_price > 0:
        pt = smart_price_threshold(our_price, comp_price, default_tol)
        diff = round(our_price - comp_price, 2)
        if diff > pt:
            return PriceDecision(DECISION_RAISE, diff, pt, ActionType.RAISE)
        if diff < -pt:
            return PriceDecision(DECISION_LOWER, diff, pt, ActionType.LOWER)
        return PriceDecision(DECISION_APPROVED, diff, pt, ActionType.APPROVE)
    return PriceDecision(DECISION_NO_COMP_PRICE, 0.0, 0.0, ActionType.REVIEW)


def undercut_price(comp_price: float, strategy: str = "standard") -> float:
    """سعر مقترح يقلّ عن المنافس بذكاء حسب الاستراتيجية.

    Args:
        comp_price: سعر المنافس.
        strategy: "standard" (-1 ريال) | "aggressive" (-2%) | "minimal" (-1%).
    """
    if comp_price <= 0:
        return 0.0
    if strategy == "aggressive":
        return max(round(comp_price * (1.0 - _SMART_UNDERCUT_PCT), 2), 0.0)
    if strategy == "minimal":
        return max(round(comp_price * (1.0 - _MATCH_TOLERANCE), 2), 0.0)
    return max(round(comp_price - 1, 2), 0.0)


def suggested_price_with_margin(comp_price: float, margin_pct: float) -> float:
    """سعر مقترح بهامش فوق سعر المنافس."""
    return round(comp_price * (1 + margin_pct / 100), 0) if comp_price > 0 else 0.0


# ════════════════════════════════════════════════════════════════════
#  محرك التسعير الذكي v2
# ════════════════════════════════════════════════════════════════════

class SmartPricingEngine:
    """محرك تسعير ذكي يدمج البيانات الاستخباراتية في القرار السعري."""

    def __init__(
        self,
        competitor_modeler: Optional[_CompetitorModelerLike] = None,
        price_predictor: Optional[_PricePredictorLike] = None,
        seasonal_engine: Optional[_SeasonalEngineLike] = None,
        price_monitor: Optional[_PriceMonitorLike] = None,
    ) -> None:
        self._competitor_modeler = competitor_modeler
        self._price_predictor = price_predictor
        self._seasonal_engine = seasonal_engine
        self._price_monitor = price_monitor

    # ── تجميع أسعار المنافسين ───────────────────────────────────

    @staticmethod
    def aggregate_competitor_prices(prices: dict[str, float], method: str = "weighted") -> float:
        """يجمع أسعار متعددين إلى سعر مرجع واحد.

        Args:
            prices: {اسم_المتجر: السعر}
            method: "min" | "max" | "avg" | "median" | "weighted"

        Returns:
            السعر المرجع المُجمَّع.
        """
        valid = {k: v for k, v in prices.items() if v > 0}
        if not valid:
            return 0.0
        vals = list(valid.values())
        if method == "min":
            return min(vals)
        if method == "max":
            return max(vals)
        if method == "median":
            s = sorted(vals)
            n = len(s)
            mid = n // 2
            if n % 2 == 0:
                return (s[mid - 1] + s[mid]) / 2
            return s[mid]
        if method == "weighted":
            # الوزن: أرخص سعر يأخذ وزناً أكبر (70%) + المتوسط (30%)
            min_v = min(vals)
            avg_v = sum(vals) / len(vals)
            return round(min_v * 0.7 + avg_v * 0.3, 2)
        return sum(vals) / len(vals)

    # ── حساب السعر المقترح الذكي ────────────────────────────────

    def compute_suggested_price(self, ctx: PricingContext) -> tuple[float, str]:
        """يحسب السعر المقترح بناءً على السياق الغني.

        Returns:
            (السعر_المقترح, سبب_الاقتراح)
        """
        comp = ctx.competitor_info
        if comp.count == 0:
            return 0.0, "لا يوجد سعر منافس"

        # السعر المرجع (weighted افتراضياً)
        reference = comp.min_price if comp.count == 1 else self.aggregate_competitor_prices(comp.prices, "weighted")

        # 1. استراتيجية حسب سلوك المنافس
        strategy_note = ""
        if ctx.competitor_strategy == "aggressive":
            # منافس عدواني ⇒ تقويض أذكى
            base = undercut_price(reference, "aggressive")
            strategy_note = "تقويض ضد منافس عدواني"
        elif ctx.competitor_strategy == "premium":
            # منافس متميز ⇒ نطابق السعر أو نقلّ قليلاً
            base = undercut_price(reference, "minimal")
            strategy_note = "مطابقة ذكية مع منافس متميز"
        elif ctx.competitor_strategy == "erratic":
            # منافس متقلب ⇒ نحتفظ بهامش أمان
            base = round(reference * 0.98, 2)
            strategy_note = "هامش أمان ضد منافس متقلب"
        else:
            # معتدل ⇒ التقويض القياسي
            base = undercut_price(reference, "standard")
            strategy_note = "تقويض قياسي"

        # 2. مراعاة اتجاه السعر
        trend_adjustment = 1.0
        if ctx.trend_direction == "rising":
            trend_adjustment = 1.0 + (_TREND_WEIGHT * abs(ctx.trend_slope_pct) / 100)
        elif ctx.trend_direction == "falling":
            trend_adjustment = 1.0 - (_TREND_WEIGHT * abs(ctx.trend_slope_pct) / 100)

        adjusted = base * trend_adjustment
        trend_note = ""
        if trend_adjustment != 1.0:
            trend_note = f" (+تعديل اتجاه {ctx.trend_direction})"

        # 3. مراعاة الموسم
        seasonal_adjustment = 1.0 + (ctx.season_adjustment_pct / 100.0)
        adjusted = adjusted * seasonal_adjustment
        season_note = ""
        if ctx.season_adjustment_pct != 0:
            season_note = f" (+تعديل موسمي {ctx.season_adjustment_pct:+.0f}%)"

        # 4. حماية الهامش (الأرضية)
        floor = self._price_floor(ctx.cost_price)
        if adjusted < floor:
            adjusted = floor
            strategy_note += f" [أرضية ربح {floor:.2f}]"

        # 5. السقف المنطقي
        ceiling = self._price_ceiling(ctx.our_price, reference)
        if ceiling is not None and adjusted > ceiling:
            adjusted = ceiling
            strategy_note += f" [سقف منطقي {ceiling:.2f}]"

        final = round(max(0.0, adjusted), 2)
        reason = f"{strategy_note}{trend_note}{season_note}"
        return final, reason

    # ── حماية الهامش ────────────────────────────────────────────

    @staticmethod
    def _price_floor(cost_price: float) -> float:
        """أرضية الربح = التكلفة + الهامش الأدنى."""
        if cost_price <= 0:
            return 0.0
        by_amount = cost_price + _MARGIN_MIN_ABS
        by_pct = cost_price * (1.0 + _MARGIN_MIN_PCT / 100.0)
        return round(max(by_amount, by_pct), 2)

    @staticmethod
    def _price_ceiling(our_price: float, reference: float) -> Optional[float]:
        """سقف منطقي لمنع المبالغة."""
        refs = [p for p in (our_price, reference) if p > 0]
        if not refs:
            return None
        return round(max(refs) * _MAX_PRICE_FACTOR, 2)

    # ── قرار ذكي متكامل ─────────────────────────────────────────

    def smart_decide(self, ctx: PricingContext, default_tol: float = PRICE_TOLERANCE) -> PriceDecision:
        """قرار سعري ذكي يدمج الاستخبارات."""
        comp = ctx.competitor_info
        if comp.count == 0:
            return PriceDecision(DECISION_NO_COMP_PRICE, 0.0, 0.0, ActionType.REVIEW)

        # السعر المرجع للمقارنة (weighted)
        ref_price = self.aggregate_competitor_prices(comp.prices, "weighted")

        # تعديل العتبة حسب عدد المنافسين (أكثر منافسين = عتبة أضيق)
        adjusted_tol = default_tol
        if comp.count >= 5:
            adjusted_tol = default_tol * 0.8
        elif comp.count == 1:
            adjusted_tol = default_tol * 1.3

        pt = smart_price_threshold(ctx.our_price, ref_price, adjusted_tol)
        diff = round(ctx.our_price - ref_price, 2)

        if diff > pt:
            return PriceDecision(DECISION_RAISE, diff, pt, ActionType.RAISE)
        if diff < -pt:
            return PriceDecision(DECISION_LOWER, diff, pt, ActionType.LOWER)
        return PriceDecision(DECISION_APPROVED, diff, pt, ActionType.APPROVE)


# ════════════════════════════════════════════════════════════════════
#  خدمة التسعير الرئيسية (متوافقة مع الإصدار السابق)
# ════════════════════════════════════════════════════════════════════

class PricingService:
    """خدمة التسعير v2: تحوّل سعرَين (أو مجموعة منافسين) إلى قرار + PriceDiff.

    يدعم الآن:
    - سعر منافس واحد (متوافق عكسياً)
    - مجموعة أسعار منافسين (جديد v2)
    - دمج الاستخبارات (اتجاهات + منافسين + مواسم)
    - كاش للقرارات
    """

    def __init__(
        self,
        default_tol: float = PRICE_TOLERANCE,
        *,
        competitor_modeler: Optional[Any] = None,
        price_predictor: Optional[Any] = None,
        seasonal_engine: Optional[Any] = None,
        price_monitor: Optional[Any] = None,
        use_cache: bool = True,
    ) -> None:
        self._tol = default_tol
        self._engine = SmartPricingEngine(
            competitor_modeler=competitor_modeler,
            price_predictor=price_predictor,
            seasonal_engine=seasonal_engine,
            price_monitor=price_monitor,
        )
        self._cache = PricingCache() if use_cache else None
        self._decisions_count = 0
        self._cache_hits = 0

    # ── الواجهة الأصلية (متوافقة 100%) ──────────────────────────

    def evaluate(
        self,
        product_id: str,
        our_price: float,
        comp_price: float,
    ) -> tuple[PriceDecision, PriceDiff]:
        """يُعيد (القرار، نموذج PriceDiff) لمنتج واحد — متوافق مع الإصدار السابق."""
        # كاش
        if self._cache is not None:
            cached = self._cache.get(product_id, our_price, {"comp": comp_price})
            if cached is not None:
                self._cache_hits += 1
                return cached

        decision = decide_price(our_price, comp_price, self._tol)
        diff_pct = (decision.diff / comp_price * 100.0) if comp_price > 0 else 0.0
        suggested = (
            undercut_price(comp_price)
            if decision.action == ActionType.RAISE
            else None
        )
        price_diff = PriceDiff(
            product_id=product_id,
            our_price=our_price,
            competitor_price=comp_price,
            diff=decision.diff,
            diff_pct=round(diff_pct, 2),
            suggested_price=suggested,
            action=decision.action,
        )
        result = (decision, price_diff)

        if self._cache is not None:
            self._cache.set(product_id, our_price, {"comp": comp_price}, result)
        self._decisions_count += 1
        return result

    # ── واجهة جديدة: سياق غني ───────────────────────────────────

    def evaluate_smart(
        self,
        ctx: PricingContext,
    ) -> tuple[PriceDecision, PriceDiff]:
        """تقييم ذكي مع سياق غني (منافسين متعددين + استخبارات)."""
        # كاش
        if self._cache is not None and ctx.competitor_info.prices:
            cached = self._cache.get(ctx.product_id, ctx.our_price, ctx.competitor_info.prices)
            if cached is not None:
                self._cache_hits += 1
                return cached

        decision = self._engine.smart_decide(ctx, self._tol)

        # السعر المرجع
        ref_price = self._engine.aggregate_competitor_prices(ctx.competitor_info.prices, "weighted")
        diff_pct = (decision.diff / ref_price * 100.0) if ref_price > 0 else 0.0

        # السعر المقترح الذكي
        suggested: Optional[float] = None
        suggested_reason = ""
        if decision.action == ActionType.RAISE:
            suggested, suggested_reason = self._engine.compute_suggested_price(ctx)
        elif decision.action == ActionType.LOWER:
            # فرصة لرفع السعر بذكاء إذا كان الاتجاه صاعد
            if ctx.trend_direction == "rising" and ctx.trend_slope_pct > 2.0:
                suggested = round(ref_price * 0.99, 2)
                suggested_reason = "رفع تدريجي مع اتجاه السوق الصاعد"
            else:
                suggested = round(ref_price * 0.98, 2)
                suggested_reason = "تعديل لمواكبة السوق"

        price_diff = PriceDiff(
            product_id=ctx.product_id,
            our_price=ctx.our_price,
            competitor_price=ref_price,
            diff=decision.diff,
            diff_pct=round(diff_pct, 2),
            suggested_price=suggested,
            action=decision.action,
        )
        result = (decision, price_diff)

        if self._cache is not None and ctx.competitor_info.prices:
            self._cache.set(ctx.product_id, ctx.our_price, ctx.competitor_info.prices, result)
        self._decisions_count += 1
        return result

    # ── واجهة جديدة: دفعة منتجات ───────────────────────────────

    def evaluate_batch(
        self,
        contexts: list[PricingContext],
    ) -> list[tuple[PriceDecision, PriceDiff]]:
        """يعالج مجموعة منتجات دفعةً واحدة."""
        return [self.evaluate_smart(ctx) for ctx in contexts]

    # ── أدوات مساعدة ────────────────────────────────────────────

    @property
    def stats(self) -> dict[str, Any]:
        """إحصاءات الخدمة."""
        return {
            "decisions_count": self._decisions_count,
            "cache_hits": self._cache_hits,
            "cache_size": self._cache.size() if self._cache else 0,
            "hit_rate": round(self._cache_hits / max(1, self._decisions_count + self._cache_hits), 3),
        }

    def clear_cache(self) -> None:
        """يفرغ كاش القرارات."""
        if self._cache:
            self._cache.clear()

    # ── بناء السياق من DataFrame ────────────────────────────────

    @staticmethod
    def build_context_from_row(
        row: Any,
        product_id: str,
        our_price_col: str = "السعر",
        comp_price_col: str = "سعر_المنافس",
        comp_store_col: str = "المنافس",
        brand_col: str = "الماركة",
        name_col: str = "المنتج",
    ) -> PricingContext:
        """يبني سياق تسعير من صف DataFrame."""
        our_price = float(row.get(our_price_col, 0) or 0)
        comp_price = float(row.get(comp_price_col, 0) or 0)
        comp_store = str(row.get(comp_store_col, "unknown"))
        brand = str(row.get(brand_col, ""))
        name = str(row.get(name_col, ""))

        comp_info = CompetitorPriceInfo()
        if comp_price > 0:
            comp_info = CompetitorPriceInfo.from_dict({comp_store: comp_price})

        return PricingContext(
            product_id=product_id,
            product_name=name,
            our_price=our_price,
            brand=brand,
            competitor_info=comp_info,
        )


# ════════════════════════════════════════════════════════════════════
#  محرك التسعير الديناميكي v3 — خوارزميات متقدمة
# ════════════════════════════════════════════════════════════════════

# معاملات التسعير الديناميكي
_ELASTICITY_DEFAULT = -1.5          # مرونة السعر الافتراضية (عطور = غير مرنة نسبياً)
_ELASTICITY_LUXURY = -0.8           # العطور الفاخرة أقل مرونة
_ELASTICITY_MASS = -2.2             # العطور الشعبية أكثر مرونة
_INVENTORY_LOW_THRESHOLD = 10       # مخزون منخفض (أقل من 10 قطع)
_INVENTORY_HIGH_THRESHOLD = 100     # مخزون مرتفع (أكثر من 100 قطعة)
_INVENTORY_SCARCITY_BONUS = 0.05    # علاوة ندرة 5%
_INVENTORY_CLEARANCE_DISCOUNT = 0.10 # تخفيض تفريغ 10%
_PEAK_HOURS = {20, 21, 22, 23}     # ساعات الذروة (8م-11م)
_WEEKEND_DAYS = {4, 5}              # الجمعة والسبت في السعودية
_WEEKEND_BONUS = 0.03               # علاوة نهاية الأسبوع 3%
_PEAK_HOUR_BONUS = 0.02             # علاوة ساعات الذروة 2%


@dataclass
class DemandSignal:
    """إشارة طلب تُستخدم في التسعير الديناميكي."""

    elasticity: float = _ELASTICITY_DEFAULT
    inventory_qty: int = 0
    days_in_stock: int = 0
    sales_velocity: float = 0.0         # قطعة/يوم
    views_count: int = 0
    cart_adds: int = 0
    conversion_rate: float = 0.0
    is_new_arrival: bool = False
    is_discontinued: bool = False


@dataclass
class MarketPosition:
    """هدف الموقع السعري في السوق."""

    target: str = "competitive"       # cheapest | competitive | premium | match_leader
    tolerance_pct: float = 5.0        # نسبة التسامح حول الهدف
    min_gap_pct: float = 0.0           # أقل فارق مسموح عن المنافس الأرخص


@dataclass
class DynamicPricingContext:
    """سياق غني للتسعير الديناميكي — يدمج كل العوامل."""

    base_ctx: PricingContext = field(default_factory=PricingContext)
    demand: DemandSignal = field(default_factory=DemandSignal)
    position: MarketPosition = field(default_factory=MarketPosition)
    current_hour: int = 0
    current_weekday: int = 0          # 0=الأحد, 4=الجمعة, 5=السبت
    historical_sales: list[float] = field(default_factory=list)


class DynamicPricingEngine:
    """محرك تسعير ديناميكي متقدم — يدمج المرونة + المخزون + الوقت + الموقع السعري.

    يحسب السعر الأمثل الذي يوازن بين:
    - تنافسية السعر مقابل المنافسين
    - حماية الهامش
    - حالة المخزون (ندرة / تكدس)
    - أنماط الطلب الزمنية
    - الموقع السعري المستهدف في السوق
    """

    def __init__(self, smart_engine: Optional[SmartPricingEngine] = None) -> None:
        self._smart = smart_engine or SmartPricingEngine()

    # ── حساب المرونة السعرية ──────────────────────────────────────

    @staticmethod
    def estimate_elasticity(
        brand: str = "",
        category: str = "perfumes",
        price_tier: str = "mid",
    ) -> float:
        """يقدّر مرونة السعر (elasticity) للمنتج.

        العطور الفاخرة (luxury) أقل مرونة (−0.8) — العملاء أقل حساسية للسعر.
        العطور الشعبية أكثر مرونة (−2.2) — العملاء يقارنون أسعاراً بكثرة.
        """
        luxury_brands = {
            "creed", "amouage", "xerjoff", "roja", "parfums de marly",
            "kilian", "clive christian", "tom ford", "bond no 9",
            "كريد", "أمواج", "زيرجوف", "روجا", "مارلي", "كيليان",
            "توم فورد", "نيشان",
        }
        brand_lower = brand.lower().strip()
        if any(lb in brand_lower for lb in luxury_brands):
            return _ELASTICITY_LUXURY
        if price_tier == "budget" or category in {"body_mist", "deodorant"}:
            return _ELASTICITY_MASS
        return _ELASTICITY_DEFAULT

    # ── تعديل المخزون ─────────────────────────────────────────────

    @staticmethod
    def inventory_adjustment(
        qty: int,
        days_in_stock: int = 0,
        sales_velocity: float = 0.0,
    ) -> tuple[float, str]:
        """يحسب عامل تعديل السعر بناءً على حالة المخزون.

        Returns:
            (عامل_التعديل, السبب)
        """
        if qty <= 0:
            return 1.0, "نفاذ المخزون — لا تعديل"
        # مخزون منخفض + سرعة مبيعات عالية ⇒ ندرة ⇒ رفع سعر
        if qty <= _INVENTORY_LOW_THRESHOLD:
            if sales_velocity > 1.0:
                return (
                    1.0 + _INVENTORY_SCARCITY_BONUS,
                    f"ندرة مخزون ({qty} قطع، مبيعات سريعة) → +{_INVENTORY_SCARCITY_BONUS:.0%}",
                )
            return 1.0, f"مخزون منخفض ({qty}) — لا تعديل"

        # مخزون مرتفع + بطء مبيعات ⇒ تكدس ⇒ تخفيض
        if qty >= _INVENTORY_HIGH_THRESHOLD and sales_velocity < 0.3:
            days_to_clear = qty / max(sales_velocity, 0.1)
            if days_to_clear > 90:
                return (
                    1.0 - _INVENTORY_CLEARANCE_DISCOUNT,
                    f"تكدس مخزون ({qty} قطع، {days_to_clear:.0f} يوم للتفريغ) → −{_INVENTORY_CLEARANCE_DISCOUNT:.0%}",
                )
            return (
                1.0 - (_INVENTORY_CLEARANCE_DISCOUNT / 2),
                f"بطء مبيعات ({qty} قطع) → −{_INVENTORY_CLEARANCE_DISCOUNT / 2:.0%}",
            )

        return 1.0, ""

    # ── تعديل الوقت (ساعات الذروة / نهاية الأسبوع) ─────────────────

    @staticmethod
    def time_adjustment(
        hour: int = 0,
        weekday: int = 0,
    ) -> tuple[float, str]:
        """يحسب تعديل السعر حسب الوقت.

        ساعات الذروة (8-11 مساءً) وجمعة/سبت ⇒ علاوة طفيفة.
        """
        factor = 1.0
        reasons: list[str] = []
        if hour in _PEAK_HOURS:
            factor += _PEAK_HOUR_BONUS
            reasons.append(f"ساعة ذروة (+{_PEAK_HOUR_BONUS:.0%})")
        if weekday in _WEEKEND_DAYS:
            factor += _WEEKEND_BONUS
            reasons.append(f"نهاية أسبوع (+{_WEEKEND_BONUS:.0%})")
        return factor, " · ".join(reasons) if reasons else ""

    # ── تحديد الموقع السعري المستهدف ──────────────────────────────

    def target_position_price(
        self,
        comp_info: CompetitorPriceInfo,
        position: MarketPosition,
        our_cost: float = 0.0,
    ) -> tuple[float, str]:
        """يحسب السعر المستهدف لتحقيق موقع سعري معين في السوق.

        Args:
            comp_info: معلومات أسعار المنافسين.
            position: الموقع المستهدف (cheapest | competitive | premium | match_leader).
            our_cost: تكلفة المنتج (لحماية الهامش).

        Returns:
            (السعر_المستهدف, السبب)
        """
        if comp_info.count == 0:
            return 0.0, "لا يوجد منافسين — لا يمكن تحديد الموقع"

        min_p = comp_info.min_price
        max_p = comp_info.max_price
        avg_p = comp_info.avg_price
        floor = SmartPricingEngine._price_floor(our_cost)

        target = position.target
        if target == "cheapest":
            # أرخص من الجميع بـ min_gap_pct
            price = round(min_p * (1.0 - position.min_gap_pct / 100), 2)
            price = max(price, floor)
            return price, f"هدف: أرخص سعر (تحت {min_p:.2f} بـ {position.min_gap_pct}%+)"

        if target == "competitive":
            # بين الأقل والمتوسط
            price = round((min_p + avg_p) / 2, 2)
            price = max(price, floor)
            return price, f"هدف: سعر تنافسي (متوسط {min_p:.2f} و {avg_p:.2f})"

        if target == "premium":
            # أعلى من المتوسط بـ tolerance_pct
            price = round(avg_p * (1.0 + position.tolerance_pct / 100), 2)
            return price, f"هدف: سعر متميز (فوق المتوسط {avg_p:.2f} بـ {position.tolerance_pct}%+)"

        if target == "match_leader":
            # مطابقة المتجر الأرخص
            price = round(min_p, 2)
            price = max(price, floor)
            return price, f"هدف: مطابقة المتجر الأرخص ({min_p:.2f})"

        return round(avg_p, 2), "هدف افتراضي: مطابقة المتوسط"

    # ── الأمثلية: سعر يعظم الإيرادات المتوقعة ─────────────────────

    def optimize_revenue(
        self,
        ctx: DynamicPricingContext,
    ) -> tuple[float, str]:
        """يحسب السعر الذي يعظم الإيرادات المتوقعة.

        نموذج مبسط:
        الإيراد = السعر × الطلب المتوقع
        الطلب المتوقع = الطلب الأساسي × (السعر المرجع / السعر)^|مرونة|

        يحل معادلة الإيراد ويختار السعر الأمثل ضمن [أرضية، سقف].
        """
        comp = ctx.base_ctx.competitor_info
        if comp.count == 0:
            return ctx.base_ctx.our_price, "لا يوجد منافسين — الإبقاء على السعر الحالي"

        ref = self._smart.aggregate_competitor_prices(comp.prices, "weighted")
        floor = SmartPricingEngine._price_floor(ctx.base_ctx.cost_price)
        ceiling = SmartPricingEngine._price_ceiling(ctx.base_ctx.our_price, ref)
        if ceiling is None:
            ceiling = ref * 2.0

        elasticity = ctx.demand.elasticity if ctx.demand.elasticity != 0 else _ELASTICITY_DEFAULT
        # نتجاهل الإشارة — نستخدم القيمة المطلقة في الصيغة
        abs_elasticity = abs(elasticity)

        # السعر الأمثل النظري (نموذج monopolist pricing)
        # p* = c / (1 - 1/|E|)  حيث c = تكلفة حدية (نفترضها = أرضية)
        # لكننا نستخدم مرونة السعر ضد السعر المرجع
        if abs_elasticity > 1.01:
            optimal = ref * (abs_elasticity / (abs_elasticity - 1.0))
        elif abs_elasticity > 0.99:
            # تجنب القسمة على صفر عند |E| ≈ 1
            optimal = ref * 2.0
        else:
            optimal = ref * 1.1

        # تقييد ضمن الحدود
        optimal = max(floor, min(optimal, ceiling))

        # تطبيق تعديلات المخزون والوقت
        inv_factor, inv_reason = self.inventory_adjustment(
            ctx.demand.inventory_qty,
            ctx.demand.days_in_stock,
            ctx.demand.sales_velocity,
        )
        time_factor, time_reason = self.time_adjustment(
            ctx.current_hour,
            ctx.current_weekday,
        )

        final = optimal * inv_factor * time_factor
        final = max(floor, min(final, ceiling))

        reasons = [f"أمثلية إيرادات (مرونة={elasticity:.1f})"]
        if inv_reason:
            reasons.append(inv_reason)
        if time_reason:
            reasons.append(time_reason)

        return round(final, 2), " · ".join(reasons)

    # ── قرار ديناميكي متكامل ─────────────────────────────────────

    def dynamic_decide(
        self,
        ctx: DynamicPricingContext,
        default_tol: float = PRICE_TOLERANCE,
    ) -> tuple[PriceDecision, PriceDiff]:
        """قرار تسعير ديناميكي متكامل — يجمع كل العوامل."""
        base = ctx.base_ctx

        # 1. القرار الأساسي من المحرك الذكي
        decision = self._smart.smart_decide(base, default_tol)

        # 2. السعر المرجع
        ref = self._smart.aggregate_competitor_prices(base.competitor_info.prices, "weighted")

        # 3. حساب السعر المقترح الديناميكي
        suggested, reason = self.optimize_revenue(ctx)

        # 4. إذا كان القرار "موافق" لكن الاقتراح الديناميكي يختلف بشكل كبير ⇒ مراجعة
        if decision.action == ActionType.APPROVE and suggested > 0:
            diff_from_current = abs(suggested - base.our_price) / base.our_price if base.our_price > 0 else 0
            if diff_from_current > 0.15:  # اختلاف > 15%
                decision = PriceDecision(
                    "⚠️ تحت المراجعة — تعديل ديناميكي كبير",
                    round(base.our_price - suggested, 2),
                    default_tol,
                    ActionType.REVIEW,
                )
                reason += " [تعديل كبير يحتاج مراجعة]"

        diff_pct = (decision.diff / ref * 100.0) if ref > 0 else 0.0

        price_diff = PriceDiff(
            product_id=base.product_id,
            our_price=base.our_price,
            competitor_price=ref,
            diff=decision.diff,
            diff_pct=round(diff_pct, 2),
            suggested_price=suggested if suggested > 0 else None,
            action=decision.action,
        )
        return decision, price_diff


# ════════════════════════════════════════════════════════════════════
#  إضافات على PricingService — واجهات التسعير الديناميكي
# ════════════════════════════════════════════════════════════════════

# نُضيف طرقاً جديدة على PricingService عبر monkey-patch نظيف
# أو يمكن استخدام DynamicPricingEngine مستقلاً

class PricingServiceV3(PricingService):
    """نسخة ممتدة من PricingService تدعم التسعير الديناميكي الكامل.

    ترث كل الواجهات السابقة وتضيف:
    - evaluate_dynamic() — تقييم ديناميكي متكامل
    - target_position() — تحديد موقع سعري مستهدف
    - optimize_batch() — أمثلية دفعية لمجموعة منتجات
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._dynamic_engine = DynamicPricingEngine(self._engine)

    # ── التقييم الديناميكي ──────────────────────────────────────

    def evaluate_dynamic(
        self,
        ctx: DynamicPricingContext,
    ) -> tuple[PriceDecision, PriceDiff]:
        """تقييم ديناميكي متكامل — يدمج المرونة + المخزون + الوقت + الموقع."""
        # كاش
        if self._cache is not None and ctx.base_ctx.competitor_info.prices:
            cached = self._cache.get(
                ctx.base_ctx.product_id,
                ctx.base_ctx.our_price,
                ctx.base_ctx.competitor_info.prices,
            )
            if cached is not None:
                self._cache_hits += 1
                return cached

        result = self._dynamic_engine.dynamic_decide(ctx, self._tol)

        if self._cache is not None and ctx.base_ctx.competitor_info.prices:
            self._cache.set(
                ctx.base_ctx.product_id,
                ctx.base_ctx.our_price,
                ctx.base_ctx.competitor_info.prices,
                result,
            )
        self._decisions_count += 1
        return result

    # ── تحديد الموقع السعري ──────────────────────────────────────

    def target_position(
        self,
        comp_info: CompetitorPriceInfo,
        position: MarketPosition,
        our_cost: float = 0.0,
    ) -> tuple[float, str]:
        """يحسب السعر المستهدف لتحقيق موقع سعري معين."""
        return self._dynamic_engine.target_position_price(comp_info, position, our_cost)

    # ── الأمثلية الدفعية ──────────────────────────────────────────

    def optimize_batch(
        self,
        contexts: list[DynamicPricingContext],
    ) -> list[tuple[PriceDecision, PriceDiff]]:
        """يعالج مجموعة منتجات بأمثلية ديناميكية."""
        return [self.evaluate_dynamic(ctx) for ctx in contexts]

    # ── بناء السياق الديناميكي ───────────────────────────────────

    @staticmethod
    def build_dynamic_context(
        row: Any,
        product_id: str,
        *,
        our_price_col: str = "السعر",
        comp_price_col: str = "سعر_المنافس",
        comp_store_col: str = "المنافس",
        brand_col: str = "الماركة",
        name_col: str = "المنتج",
        inventory_qty: int = 0,
        sales_velocity: float = 0.0,
        hour: int = 0,
        weekday: int = 0,
    ) -> DynamicPricingContext:
        """يبني سياق ديناميكي كامل من صف DataFrame + إشارات الطلب."""
        base = PricingService.build_context_from_row(
            row,
            product_id,
            our_price_col=our_price_col,
            comp_price_col=comp_price_col,
            comp_store_col=comp_store_col,
            brand_col=brand_col,
            name_col=name_col,
        )
        elasticity = DynamicPricingEngine.estimate_elasticity(
            brand=base.brand,
            price_tier="luxury" if base.brand.lower() in {
                "creed", "amouage", "xerjoff", "roja", "parfums de marly",
                "كريد", "أمواج", "زيرجوف", "روجا", "مارلي",
            } else "mid",
        )
        demand = DemandSignal(
            elasticity=elasticity,
            inventory_qty=inventory_qty,
            sales_velocity=sales_velocity,
        )
        return DynamicPricingContext(
            base_ctx=base,
            demand=demand,
            current_hour=hour,
            current_weekday=weekday,
        )


# ════════════════════════════════════════════════════════════════════
#  أدوات مساعدة للتحليل السعري
# ════════════════════════════════════════════════════════════════════

def price_sensitivity_analysis(
    our_price: float,
    competitor_prices: dict[str, float],
    elasticity: float = _ELASTICITY_DEFAULT,
) -> dict[str, Any]:
    """يحلل حساسية السعر ويوصي بسعر أمثل.

    Args:
        our_price: السعر الحالي.
        competitor_prices: أسعار المنافسين.
        elasticity: معامل المرونة (سالب = عكسي).

    Returns:
        قاموس يحتوي التوصية والتحليل.
    """
    valid = {k: v for k, v in competitor_prices.items() if v > 0}
    if not valid:
        return {"recommendation": "لا يوجد بيانات منافسين", "optimal_price": our_price}

    min_p = min(valid.values())
    avg_p = sum(valid.values()) / len(valid)
    abs_e = abs(elasticity)

    # السعر الأمثل النظري
    if abs_e > 1.01:
        optimal = avg_p * (abs_e / (abs_e - 1.0))
    elif abs_e > 0.99:
        optimal = avg_p * 2.0
    else:
        optimal = avg_p * 1.05

    # موقعنا الحالي
    position = "competitive"
    if our_price <= min_p:
        position = "cheapest"
    elif our_price > avg_p * 1.2:
        position = "premium"
    elif our_price > avg_p:
        position = "expensive"

    # التوصية
    recommendation = "الإبقاء"
    if optimal < our_price * 0.9:
        recommendation = "خفض السعر"
    elif optimal > our_price * 1.1:
        recommendation = "رفع السعر"

    return {
        "our_price": our_price,
        "competitor_min": min_p,
        "competitor_avg": round(avg_p, 2),
        "elasticity": elasticity,
        "optimal_price": round(optimal, 2),
        "position": position,
        "recommendation": recommendation,
        "potential_revenue_change": round(
            ((optimal / max(our_price, 0.01)) ** (1 - abs_e) - 1) * 100, 2
        ) if our_price > 0 else 0,
    }


def competitive_index(
    our_price: float,
    competitor_prices: dict[str, float],
) -> dict[str, Any]:
    """يحسب مؤشر التنافسية (0-100) ومقاييس السوق.

    Returns:
        {index: 0-100, rank: N, total_competitors: M, gap_pct: %}
    """
    valid = {k: v for k, v in competitor_prices.items() if v > 0}
    if not valid or our_price <= 0:
        return {"index": 0, "rank": 0, "total_competitors": 0, "gap_pct": 0}

    all_prices = sorted(list(valid.values()) + [our_price])
    rank = len([p for p in all_prices if p < our_price]) + 1
    total = len(all_prices)
    avg_comp = sum(valid.values()) / len(valid)
    gap_pct = round((our_price - avg_comp) / avg_comp * 100, 2) if avg_comp > 0 else 0

    # مؤشر التنافسية: 100 = أرخص سعر، 0 = أغلى سعر
    index = round(max(0.0, (1 - (rank - 1) / max(1, total - 1)) * 100), 1) if total > 1 else 50

    return {
        "index": index,
        "rank": rank,
        "total_competitors": total - 1,
        "gap_pct": gap_pct,
        "avg_competitor": round(avg_comp, 2),
    }
