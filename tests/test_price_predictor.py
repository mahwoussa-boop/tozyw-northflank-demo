"""tests/test_price_predictor.py — اختبارات شاملة لخدمة تنبؤ اتجاه الأسعار.

يغطي: التنبؤ (صاعد/هابط/مستقر/متقلب)، البيانات الأدنى، الدفعي،
اكتشاف الانعكاس، التقلب، والتسلسل.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from infrastructure.db_manager import DatabaseManager
from services.intelligence.price_predictor import PriceTrend, PriceTrendPredictor


# ════════════════════════════════════════════════════════════════════
#  Fixtures
# ════════════════════════════════════════════════════════════════════

@pytest.fixture()
def db() -> DatabaseManager:
    """ينشئ مدير قاعدة بيانات مؤقت للاختبار."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    manager = DatabaseManager(path)
    yield manager
    manager.close()
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture()
def predictor(db: DatabaseManager) -> PriceTrendPredictor:
    """ينشئ محرك تنبؤ للاختبار."""
    return PriceTrendPredictor(db)


# ════════════════════════════════════════════════════════════════════
#  اختبارات التنبؤ بالاتجاه
# ════════════════════════════════════════════════════════════════════

class TestPredictTrend:
    """اختبارات predict()."""

    def test_predict_rising_trend(self, predictor: PriceTrendPredictor) -> None:
        """أسعار صاعدة بوضوح → direction = rising."""
        prices = [100, 105, 110, 115, 120, 125, 130, 135, 140]
        trend = predictor.predict(prices, product_name="عطر صاعد")

        assert trend.direction == "rising"
        assert trend.slope > 0
        assert trend.product_name == "عطر صاعد"
        assert trend.data_points == 9
        assert trend.current_price == 140
        assert trend.predicted_price_7d > trend.current_price
        assert trend.predicted_price_30d > trend.predicted_price_7d

    def test_predict_falling_trend(self, predictor: PriceTrendPredictor) -> None:
        """أسعار هابطة بوضوح → direction = falling."""
        prices = [200, 190, 180, 170, 160, 150, 140, 130, 120]
        trend = predictor.predict(prices, product_name="عطر هابط")

        assert trend.direction == "falling"
        assert trend.slope < 0
        assert trend.current_price == 120
        assert trend.predicted_price_7d < trend.current_price

    def test_predict_stable_trend(self, predictor: PriceTrendPredictor) -> None:
        """أسعار ثابتة تقريباً → direction = stable."""
        prices = [100, 100.1, 99.9, 100, 100.05, 99.95, 100, 100.02, 99.98]
        trend = predictor.predict(prices)

        assert trend.direction == "stable"
        assert abs(trend.slope) < 1.0  # ميل أسبوعي ضئيل

    def test_predict_volatile_prices(self, predictor: PriceTrendPredictor) -> None:
        """أسعار متقلبة جداً → direction = volatile."""
        prices = [100, 200, 50, 300, 25, 250, 75, 350, 30]
        trend = predictor.predict(prices)

        assert trend.direction == "volatile"

    def test_predict_minimum_data_3_points(self, predictor: PriceTrendPredictor) -> None:
        """3 نقاط بيانات — الحد الأدنى المقبول."""
        prices = [100, 110, 120]
        trend = predictor.predict(prices)

        assert trend.data_points == 3
        assert trend.direction in ("rising", "falling", "stable", "volatile")
        assert trend.confidence > 0

    def test_predict_insufficient_data_below_3(self, predictor: PriceTrendPredictor) -> None:
        """أقل من 3 نقاط → مستقر بثقة منخفضة."""
        # نقطتان
        trend2 = predictor.predict([100, 105])
        assert trend2.direction == "stable"
        assert trend2.confidence == pytest.approx(0.1)
        assert trend2.data_points == 2

        # نقطة واحدة
        trend1 = predictor.predict([100])
        assert trend1.direction == "stable"
        assert trend1.confidence == pytest.approx(0.1)

        # قائمة فارغة
        trend0 = predictor.predict([])
        assert trend0.direction == "stable"
        assert trend0.current_price == 0.0

    def test_predict_confidence_increases_with_data(self, predictor: PriceTrendPredictor) -> None:
        """الثقة تزداد مع زيادة نقاط البيانات."""
        short = [100, 110, 120, 130]
        long = [100 + i * 10 for i in range(30)]

        trend_short = predictor.predict(short)
        trend_long = predictor.predict(long)

        # مع خط مستقيم مثالي R²=1، الثقة مع 30 نقطة أعلى
        assert trend_long.confidence > trend_short.confidence

    def test_predict_no_negative_prices(self, predictor: PriceTrendPredictor) -> None:
        """الأسعار المتوقعة لا تكون سالبة."""
        prices = [10, 8, 6, 4, 2, 1]
        trend = predictor.predict(prices)

        assert trend.predicted_price_7d >= 0.0
        assert trend.predicted_price_30d >= 0.0


# ════════════════════════════════════════════════════════════════════
#  اختبارات التنبؤ الدفعي
# ════════════════════════════════════════════════════════════════════

class TestPredictBatch:
    """اختبارات predict_batch()."""

    def test_predict_batch_returns_list(self, predictor: PriceTrendPredictor) -> None:
        """التنبؤ الدفعي يعيد قائمة PriceTrend لكل منتج."""
        products = {
            "عطر A": [100, 110, 120, 130],
            "عطر B": [200, 195, 190, 185],
            "عطر C": [50, 50, 50, 50],
        }
        results = predictor.predict_batch(products)

        assert isinstance(results, list)
        assert len(results) == 3
        assert all(isinstance(r, PriceTrend) for r in results)

        # التحقق من الأسماء
        names = {r.product_name for r in results}
        assert names == {"عطر A", "عطر B", "عطر C"}


# ════════════════════════════════════════════════════════════════════
#  اختبارات اكتشاف الانعكاس
# ════════════════════════════════════════════════════════════════════

class TestDetectTrendReversal:
    """اختبارات detect_trend_reversal()."""

    def test_rising_to_falling(self, predictor: PriceTrendPredictor) -> None:
        """اكتشاف انعكاس من صاعد إلى هابط."""
        # 5 نقاط صاعدة ثم 5 هابطة
        prices = [100, 110, 120, 130, 140, 135, 125, 115, 105, 95]
        result = predictor.detect_trend_reversal(prices, window=5)

        assert result == "rising_to_falling"

    def test_falling_to_rising(self, predictor: PriceTrendPredictor) -> None:
        """اكتشاف انعكاس من هابط إلى صاعد."""
        prices = [200, 190, 180, 170, 160, 165, 175, 185, 195, 205]
        result = predictor.detect_trend_reversal(prices, window=5)

        assert result == "falling_to_rising"

    def test_no_reversal_consistent_trend(self, predictor: PriceTrendPredictor) -> None:
        """اتجاه ثابت → لا انعكاس (None)."""
        prices = [100, 110, 120, 130, 140, 150, 160, 170, 180, 190]
        result = predictor.detect_trend_reversal(prices, window=5)

        assert result is None

    def test_insufficient_data_for_reversal(self, predictor: PriceTrendPredictor) -> None:
        """بيانات غير كافية (أقل من نافذتين) → None."""
        result = predictor.detect_trend_reversal([100, 110, 120], window=5)
        assert result is None


# ════════════════════════════════════════════════════════════════════
#  اختبارات التقلب
# ════════════════════════════════════════════════════════════════════

class TestComputeVolatility:
    """اختبارات compute_volatility()."""

    def test_high_volatility_for_erratic(self, predictor: PriceTrendPredictor) -> None:
        """أسعار عشوائية جداً → تقلب عالي."""
        prices = [50, 200, 30, 250, 10, 300]
        vol = predictor.compute_volatility(prices)

        assert vol > 0.5  # تقلب عالي جداً

    def test_low_volatility_for_stable(self, predictor: PriceTrendPredictor) -> None:
        """أسعار ثابتة → تقلب منخفض."""
        prices = [100, 100, 100, 100, 100]
        vol = predictor.compute_volatility(prices)

        assert vol < 0.01  # شبه صفري

    def test_zero_volatility_single_point(self, predictor: PriceTrendPredictor) -> None:
        """نقطة واحدة → تقلب صفري."""
        vol = predictor.compute_volatility([100])
        assert vol == 0.0


# ════════════════════════════════════════════════════════════════════
#  اختبارات التسلسل (to_dict)
# ════════════════════════════════════════════════════════════════════

class TestPriceTrendSerialization:
    """اختبارات PriceTrend.to_dict()."""

    def test_to_dict_roundtrip(self) -> None:
        """to_dict ينتج قاموساً كاملاً قابلاً للقراءة."""
        trend = PriceTrend(
            product_name="عطر اختبار",
            direction="rising",
            slope=2.5,
            confidence=0.85,
            predicted_price_7d=115.50,
            predicted_price_30d=130.00,
            data_points=10,
            current_price=110.00,
            price_history=[100, 102, 104, 106, 108, 110],
        )
        d = trend.to_dict()

        assert isinstance(d, dict)
        assert d["product_name"] == "عطر اختبار"
        assert d["direction"] == "rising"
        assert d["slope"] == pytest.approx(2.5, abs=0.01)
        assert d["confidence"] == pytest.approx(0.85, abs=0.01)
        assert d["predicted_price_7d"] == pytest.approx(115.50, abs=0.01)
        assert d["predicted_price_30d"] == pytest.approx(130.00, abs=0.01)
        assert d["data_points"] == 10
        assert d["current_price"] == pytest.approx(110.00, abs=0.01)
        assert len(d["price_history"]) == 6
        # كل المفاتيح موجودة
        expected_keys = {
            "product_name", "direction", "slope", "confidence",
            "predicted_price_7d", "predicted_price_30d",
            "data_points", "current_price", "price_history",
        }
        assert set(d.keys()) == expected_keys
