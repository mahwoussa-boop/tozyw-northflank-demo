"""tests/test_pricing_guard.py — اختبارات سياج التسعير v1 (دالة نقية).

يشمل إلزامياً إعادة إنتاج عكسية لخطأ ``pricing_service`` v2 المعزول:
المنافس غير المتاح يُستبعَد من مرجع السعر تماماً.
"""
import pytest

from core.pricing_guard import apply_policy_v1


def _c(price, available=True):
    """عنصر منافس مختصر."""
    return {"price": price, "available": available}


def test_natural_case():
    r = apply_policy_v1([_c(100), _c(120)])
    assert r["ok"] is True
    assert r["verdict"] == "ok"
    assert r["price"] == 99.0                       # أدنى متاح (100) − 1.0
    assert r["n_available"] == 2
    assert r["n_total"] == 2
    assert r["min_available"] == 100
    assert r["median"] == 110.0
    assert r["band_low"] == pytest.approx(88.0)     # 0.8×110
    assert r["band_high"] == pytest.approx(132.0)   # 1.2×110
    assert r["floor_active"] is False


def test_v2_bug_unavailable_excluded():
    """منافس غير متاح بسعر 50 مع متاحَين 100 و120 ⇒ النتيجة = حالة المتاحَين وحدهما."""
    with_unavailable = apply_policy_v1([_c(50, available=False), _c(100), _c(120)])
    available_only = apply_policy_v1([_c(100), _c(120)])
    assert with_unavailable["price"] == available_only["price"] == 99.0
    assert with_unavailable["verdict"] == available_only["verdict"] == "ok"
    assert with_unavailable["min_available"] == 100     # ليس 50 (الخطأ المعزول)
    assert with_unavailable["median"] == 110.0
    assert with_unavailable["n_available"] == 2
    assert with_unavailable["n_total"] == 3
    # مطابقة تامّة بعد تحييد العدّ الكلي فقط:
    a, b = dict(with_unavailable), dict(available_only)
    a.pop("n_total"); b.pop("n_total")
    assert a == b


def test_clamped_to_band_low():
    # [100, 200] ⇒ أدنى−1=99، وسيط=150، حدّ أدنى=120 ⇒ 99<120 ⇒ قصّ للأدنى.
    r = apply_policy_v1([_c(100), _c(200)])
    assert r["ok"] is True
    assert r["verdict"] == "clamped_to_band_low"
    assert r["price"] == 120.0
    assert r["band_low"] == pytest.approx(120.0)


def test_no_available_market():
    r = apply_policy_v1([_c(100, available=False), _c(0, available=True)])
    assert r["ok"] is False
    assert r["price"] is None
    assert r["verdict"] == "no_available_market"
    assert r["n_available"] == 0
    assert r["n_total"] == 2


def test_floor_disabled_cost_zero():
    r = apply_policy_v1([_c(100), _c(120)], cost_price=0.0)
    assert r["floor_active"] is False
    assert r["verdict"] == "ok"                     # الأرضية لم تُفعّل
    assert r["price"] == 99.0


def test_raised_to_cost_floor():
    # التكلفة 110 داخل النطاق [88,132] وأعلى من المقترح 99 ⇒ رفع للأرضية.
    r = apply_policy_v1([_c(100), _c(120)], cost_price=110.0)
    assert r["ok"] is True
    assert r["verdict"] == "raised_to_cost_floor"
    assert r["price"] == 110.0
    assert r["floor_active"] is True


def test_blocked_cost_above_band():
    # التكلفة 200 فوق سقف النطاق 132 ⇒ حجب.
    r = apply_policy_v1([_c(100), _c(120)], cost_price=200.0)
    assert r["ok"] is False
    assert r["price"] is None
    assert r["verdict"] == "blocked_cost_above_band"
    assert r["floor_active"] is True


def test_corrupt_prices_filtered():
    r = apply_policy_v1([_c(None), _c(0), _c(-5), _c("abc"), _c(100), _c(120)])
    assert r["n_total"] == 6
    assert r["n_available"] == 2                    # فقط 100 و120
    assert r["min_available"] == 100
    assert r["price"] == 99.0
    assert r["verdict"] == "ok"


def test_single_competitor():
    r = apply_policy_v1([_c(100)])
    assert r["ok"] is True
    assert r["n_available"] == 1
    assert r["median"] == 100.0
    assert r["price"] == 99.0
    assert r["verdict"] == "ok"


def test_ceiling_clamp_never_reported_with_min_minus_one():
    """توثيق: مع (أدنى−1) لا يتجاوز base السقف أبداً (band_high=1.2×الوسيط ≥ 1.2×الأدنى)،
    فالفرع clamped_to_band_high احتياطي نظري لا يُبلَّغ في التشغيل الطبيعي."""
    cases = [
        [_c(100), _c(120)],
        [_c(10), _c(15), _c(22)],
        [_c(100)],
        [_c(50), _c(500)],
        [_c(7), _c(7), _c(7)],
    ]
    for prices in cases:
        r = apply_policy_v1(prices)
        assert r["verdict"] != "clamped_to_band_high"
        base = r["min_available"] - 1.0             # برهان رياضي مباشر
        assert base <= r["band_high"]


def test_our_price_is_inert_in_v1():
    # our_price لا تُغيّر قرار v1 (مرجعها الأقل المتوفر لا سعرنا).
    without = apply_policy_v1([_c(100), _c(120)])
    with_our = apply_policy_v1([_c(100), _c(120)], our_price=999.0)
    assert without == with_our


def test_price_rounded_two_decimals():
    # رفعٌ لأرضية تكلفة بثلاث خانات ⇒ يُقرَّب لخانتين.
    r = apply_policy_v1([_c(100), _c(120)], cost_price=110.567)
    assert r["verdict"] == "raised_to_cost_floor"
    assert r["price"] == 110.57
