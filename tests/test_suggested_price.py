"""
tests/test_suggested_price.py — اختبار «السعر المقترَح» (الدالة النقيّة فقط)
==========================================================================
يختبر ``compute_suggested_price`` **بلا أيّ قاعدة/SQL**: مدخلات أرقام مباشرة.
التأكيدات على الحدود والترتيب النسبي (لا float دقيقة حيث لا يلزم) — متينة أمام
ضبط الثوابت لاحقاً باستخدام ``BAND_PCT`` بدل رقم مثبّت.
"""
from services.opportunity_service import BAND_PCT, compute_suggested_price as csp


def test_normal_undercuts_by_one():
    """عاديّ: أرخص بريال من أقل متوفّر، داخل الحاجز."""
    assert csp(200, 200, 210) == 199


def test_floor_binds_low_prices():
    """الأرضية تُلزِم: instock=4 => raw=3 لكن median×0.8=4 تمنع النزول."""
    p = csp(4, 4, 5)
    assert p >= 5 * (1 - BAND_PCT)   # ≥ الأرضية (4.0)
    assert p == 4                    # الأرضية هي المُلزِمة (لا raw=3)


def test_full_stockout_fallback_to_price_min():
    """نفاد كامل (لا متوفّر): المرجع = price_min."""
    assert csp(None, 150, 180) == 149


def test_always_within_band_unless_cost():
    """بلا تكلفة: النتيجة دائماً داخل [median×0.8, median×1.2] (± هامش round)."""
    cases = [
        (300, 300, 250),   # ref أعلى قليلاً — لا يتجاوز السقف
        (50, 50, 500),     # ref أقل بكثير — الأرضية تُلزِم
        (210, 210, 200),
        (1000, 1000, 200), # ref أعلى بكثير — السقف يُلزِم
    ]
    for instock, pmin, median in cases:
        p = csp(instock, pmin, median)
        assert p is not None
        assert median * (1 - BAND_PCT) - 0.5 <= p <= median * (1 + BAND_PCT) + 0.5


def test_ceiling_binds_high_reference():
    """مرجع أعلى بكثير من الوسيط ⇒ السقف median×1.2 يُلزِم."""
    assert csp(1000, 1000, 200) == round(200 * (1 + BAND_PCT))   # 240


def test_cost_floor_can_exceed_band():
    """التكلفة أرضية صلبة قد ترفع السعر فوق الحاجز (اختبار سلوك الأرضية)."""
    base = csp(200, 200, 210)              # 199 (بلا تكلفة)
    with_cost = csp(200, 200, 210, cost=260)
    assert with_cost == 260
    assert with_cost > base


def test_invalid_median_returns_none():
    """وسيط غير صالح ⇒ بيانات غير كافية."""
    assert csp(200, 200, None) is None
    assert csp(200, 200, 0) is None


def test_no_valid_reference_returns_none():
    """لا مرجع صالح (لا متوفّر ولا price_min) ⇒ None."""
    assert csp(None, None, 180) is None
    assert csp(0, 0, 180) is None


def test_constants_contract():
    """عقد التصميم: الاقتطاع موجب صغير والحاجز نسبة معقولة."""
    from services.opportunity_service import UNDERCUT_SAR
    assert UNDERCUT_SAR > 0
    assert 0 < BAND_PCT < 1
