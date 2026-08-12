"""services/intelligence/price_predictor.py — محرك تنبؤ اتجاه الأسعار.

يحلل تاريخ الأسعار باستخدام انحدار خطي يدوي (بدون numpy) لتحديد:
- اتجاه السعر (صاعد / هابط / مستقر / متقلب)
- السعر المتوقع خلال 7 و 30 يوم
- معامل الثقة بناءً على عدد نقاط البيانات وانخفاض المتبقيات
- اكتشاف انعكاس الاتجاه
- حساب التقلب (معامل الاختلاف)

⚠️ لا يستورد Streamlit أبداً. لا يستخدم numpy.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from infrastructure.db_manager import DatabaseManager


# ════════════════════════════════════════════════════════════════════
#  نموذج بيانات اتجاه السعر
# ════════════════════════════════════════════════════════════════════

@dataclass
class PriceTrend:
    """نتيجة تحليل اتجاه السعر لمنتج واحد.

    Attributes:
        product_name: اسم المنتج.
        direction: اتجاه السعر (rising | falling | stable | volatile).
        slope: نسبة التغير الأسبوعي (%) — موجب = صاعد.
        confidence: معامل الثقة (0-1).
        predicted_price_7d: السعر المتوقع بعد 7 أيام.
        predicted_price_30d: السعر المتوقع بعد 30 أيام.
        data_points: عدد نقاط البيانات المستخدمة.
        current_price: السعر الحالي (آخر قيمة).
        price_history: قائمة الأسعار التاريخية.
    """

    product_name: str = ""
    direction: str = "stable"
    slope: float = 0.0
    confidence: float = 0.0
    predicted_price_7d: float = 0.0
    predicted_price_30d: float = 0.0
    data_points: int = 0
    current_price: float = 0.0
    price_history: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        """تحويل إلى قاموس للتخزين أو الإرسال عبر API."""
        return {
            "product_name": self.product_name,
            "direction": self.direction,
            "slope": round(self.slope, 4),
            "confidence": round(self.confidence, 4),
            "predicted_price_7d": round(self.predicted_price_7d, 2),
            "predicted_price_30d": round(self.predicted_price_30d, 2),
            "data_points": self.data_points,
            "current_price": round(self.current_price, 2),
            "price_history": [round(p, 2) for p in self.price_history],
        }


# ════════════════════════════════════════════════════════════════════
#  دوال رياضية مساعدة (بدون numpy)
# ════════════════════════════════════════════════════════════════════

def _mean(values: list[float]) -> float:
    """حساب المتوسط الحسابي."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def _std(values: list[float]) -> float:
    """حساب الانحراف المعياري (population std)."""
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    variance = sum((x - m) ** 2 for x in values) / len(values)
    return math.sqrt(variance)


def _linear_regression(ys: list[float]) -> tuple[float, float, float]:
    """انحدار خطي يدوي: y = a + b*x حيث x = 0, 1, 2, ...

    Returns:
        (intercept, slope, r_squared) — المقطع، الميل، ومعامل التحديد R².
    """
    n = len(ys)
    if n < 2:
        return (ys[0] if ys else 0.0), 0.0, 0.0

    xs = list(range(n))
    x_mean = _mean(xs)
    y_mean = _mean(ys)

    # حساب المقام والبسط لميل الانحدار
    numerator = sum((xs[i] - x_mean) * (ys[i] - y_mean) for i in range(n))
    denominator = sum((xs[i] - x_mean) ** 2 for i in range(n))

    if denominator == 0:
        return y_mean, 0.0, 0.0

    slope = numerator / denominator
    intercept = y_mean - slope * x_mean

    # حساب R² (معامل التحديد)
    ss_res = sum((ys[i] - (intercept + slope * xs[i])) ** 2 for i in range(n))
    ss_tot = sum((ys[i] - y_mean) ** 2 for i in range(n))

    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

    return intercept, slope, max(0.0, r_squared)


# ════════════════════════════════════════════════════════════════════
#  خدمة التنبؤ بالاتجاه
# ════════════════════════════════════════════════════════════════════

class PriceTrendPredictor:
    """محرك تنبؤ اتجاه الأسعار باستخدام انحدار خطي يدوي.

    يحلل سلاسل أسعار تاريخية ويعيد اتجاه السعر مع توقعات مستقبلية.
    يدعم اكتشاف انعكاس الاتجاه وقياس التقلب.

    Args:
        db: مدير قاعدة البيانات (للاستخدام المستقبلي في تخزين التنبؤات).
    """

    def __init__(self, db: DatabaseManager) -> None:
        """تهيئة المحرك مع مدير قاعدة البيانات.

        Args:
            db: مدير اتصال SQLite المُحقَن.
        """
        self._db = db

    # ── تنبؤ بالاتجاه ────────────────────────────────────────────

    def predict(
        self,
        prices: list[float],
        *,
        product_name: str = "",
    ) -> PriceTrend:
        """يحلل قائمة أسعار زمنية ويتنبأ بالاتجاه.

        يستخدم انحدار خطي يدوي (least squares) لحساب الميل والاتجاه.
        يحتاج 3 نقاط بيانات على الأقل للتنبؤ ذي معنى.

        Args:
            prices: قائمة أسعار مرتبة زمنياً (الأقدم أولاً).
            product_name: اسم المنتج (اختياري، للتسمية).

        Returns:
            PriceTrend: نتيجة التحليل شاملة الاتجاه والتوقعات.
        """
        # تصفية القيم غير الصالحة
        valid_prices = [p for p in prices if isinstance(p, (int, float)) and p > 0]
        n = len(valid_prices)

        # بيانات غير كافية → مستقر بثقة منخفضة
        if n < 3:
            current = valid_prices[-1] if valid_prices else 0.0
            return PriceTrend(
                product_name=product_name,
                direction="stable",
                slope=0.0,
                confidence=0.1,
                predicted_price_7d=current,
                predicted_price_30d=current,
                data_points=n,
                current_price=current,
                price_history=list(valid_prices),
            )

        current = valid_prices[-1]
        mean_price = _mean(valid_prices)

        # انحدار خطي
        intercept, slope, r_squared = _linear_regression(valid_prices)

        # تحويل الميل لنسبة أسبوعية (%)
        # الميل هو التغير لكل نقطة بيانات
        # نفترض أن كل نقطة تمثل يوماً واحداً → الميل الأسبوعي = slope * 7
        slope_pct = (slope / mean_price * 100) if mean_price > 0 else 0.0
        weekly_slope_pct = slope_pct * 7

        # تقلب (معامل الاختلاف)
        cv = self.compute_volatility(valid_prices)

        # تحديد الاتجاه
        if cv > 0.2:
            direction = "volatile"
        elif weekly_slope_pct > 1.0:
            direction = "rising"
        elif weekly_slope_pct < -1.0:
            direction = "falling"
        else:
            direction = "stable"

        # حساب الثقة
        # عوامل: عدد النقاط (أكثر = أفضل)، R² (أعلى = أفضل)
        data_factor = min(1.0, n / 30.0)  # يتشبع عند 30 نقطة
        confidence = 0.3 * data_factor + 0.7 * r_squared
        confidence = max(0.0, min(1.0, confidence))

        # توقع الأسعار المستقبلية
        # السعر عند نقطة x: intercept + slope * x
        # النقطة الحالية هي x = n - 1
        predicted_7d = intercept + slope * (n - 1 + 7)
        predicted_30d = intercept + slope * (n - 1 + 30)

        # لا نسمح بأسعار سالبة
        predicted_7d = max(0.0, predicted_7d)
        predicted_30d = max(0.0, predicted_30d)

        return PriceTrend(
            product_name=product_name,
            direction=direction,
            slope=weekly_slope_pct,
            confidence=confidence,
            predicted_price_7d=predicted_7d,
            predicted_price_30d=predicted_30d,
            data_points=n,
            current_price=current,
            price_history=list(valid_prices),
        )

    # ── تنبؤ دفعي ─────────────────────────────────────────────

    def predict_batch(
        self,
        products: dict[str, list[float]],
    ) -> list[PriceTrend]:
        """يحلل مجموعة منتجات دفعةً واحدة.

        Args:
            products: قاموس {اسم_المنتج: [تاريخ_الأسعار]}.

        Returns:
            قائمة PriceTrend لكل منتج.
        """
        results: list[PriceTrend] = []
        for name, price_list in products.items():
            trend = self.predict(price_list, product_name=name)
            results.append(trend)
        return results

    # ── اكتشاف انعكاس الاتجاه ─────────────────────────────────

    def detect_trend_reversal(
        self,
        prices: list[float],
        *,
        window: int = 5,
    ) -> Optional[str]:
        """يكتشف انعكاس الاتجاه بمقارنة ميل النافذة الأخيرة مع السابقة.

        يقارن ميل آخر ``window`` نقاط مع ميل الـ ``window`` نقاط السابقة.
        إذا تغير الاتجاه من صاعد لهابط أو العكس، يُبلَّغ عن الانعكاس.

        Args:
            prices: قائمة أسعار مرتبة زمنياً.
            window: حجم النافذة للمقارنة (الافتراضي: 5).

        Returns:
            "rising_to_falling" | "falling_to_rising" | None
        """
        valid_prices = [p for p in prices if isinstance(p, (int, float)) and p > 0]

        # نحتاج على الأقل نافذتين
        if len(valid_prices) < window * 2:
            return None

        # النافذة السابقة والحالية
        prev_window = valid_prices[-(window * 2):-window]
        curr_window = valid_prices[-window:]

        _, prev_slope, _ = _linear_regression(prev_window)
        _, curr_slope, _ = _linear_regression(curr_window)

        if prev_slope > 0 and curr_slope < 0:
            return "rising_to_falling"
        if prev_slope < 0 and curr_slope > 0:
            return "falling_to_rising"

        return None

    # ── حساب التقلب ────────────────────────────────────────────

    def compute_volatility(self, prices: list[float]) -> float:
        """يحسب معامل الاختلاف (coefficient of variation) كمقياس للتقلب.

        معامل الاختلاف = الانحراف المعياري / المتوسط.
        قيمة أعلى تعني تقلباً أكبر.

        Args:
            prices: قائمة أسعار.

        Returns:
            معامل الاختلاف (0-1+). 0 = ثابت تماماً.
        """
        valid_prices = [p for p in prices if isinstance(p, (int, float)) and p > 0]
        if len(valid_prices) < 2:
            return 0.0

        mean = _mean(valid_prices)
        if mean == 0:
            return 0.0

        std = _std(valid_prices)
        return std / mean
