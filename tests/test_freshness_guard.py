"""
tests/test_freshness_guard.py — حارس الطزاجة (دوال نقيّة فقط، بلا أيّ SQL/قاعدة)
==============================================================================
يختبر:
  • ``apply_freshness_guard``: طازج ⇒ يبقى السعر · قديم ⇒ None بسبب "stale" ·
    حدّ ``STALE_AFTER_DAYS`` بالضبط · قِدَم غير معروف/سالب ⇒ لا حجب.
  • ``format_data_age``: اليوم/أمس/قبل N يوم/غير معروف.
  • ``rank_opportunities``: القِدَم يحجب الاقتراح فقط — لا يُخفي المنتج ولا يغيّر
    الدرجة/الترتيب.
"""
from services.opportunity_service import (
    apply_freshness_guard,
    format_data_age,
    rank_opportunities,
    STALE_AFTER_DAYS,
)


# ── apply_freshness_guard ────────────────────────────────────────────────
def test_fresh_keeps_price():
    assert apply_freshness_guard(199, data_age_days=1.0) == (199, None)


def test_stale_blocks_price_with_reason():
    assert apply_freshness_guard(199, data_age_days=5.0) == (None, "stale")


def test_boundary_exactly_three_days_is_fresh():
    """الحدّ: >STALE_AFTER_DAYS = قديم؛ عند القيمة بالضبط ما زال طازجاً."""
    assert apply_freshness_guard(199, float(STALE_AFTER_DAYS)) == (199, None)
    assert apply_freshness_guard(199, STALE_AFTER_DAYS + 0.01) == (None, "stale")
    assert apply_freshness_guard(199, STALE_AFTER_DAYS - 0.01) == (199, None)


def test_unknown_age_not_blocked():
    """القِدَم غير المعروف (None) لا يُحجب (المخطط يضمن updated_at غير فارغ)."""
    assert apply_freshness_guard(199, None) == (199, None)


def test_negative_age_is_fresh():
    """فارق التوقيت قد يعطي قِدَماً سالباً طفيفاً ⇒ طازج."""
    assert apply_freshness_guard(199, -0.02) == (199, None)


def test_none_price_passthrough_and_stale():
    assert apply_freshness_guard(None, 1.0) == (None, None)      # لا سعر + طازج
    assert apply_freshness_guard(None, 9.0) == (None, "stale")   # لا سعر + قديم


# ── format_data_age ──────────────────────────────────────────────────────
def test_format_data_age():
    assert format_data_age(None) == "غير معروف"
    assert format_data_age(-0.02) == "اليوم"
    assert format_data_age(0.4) == "اليوم"
    assert format_data_age(1.2) == "أمس"
    assert format_data_age(3.7) == "قبل 3 يوم"


# ── الترتيب/الإخفاء لا يتأثّران بالقِدَم ───────────────────────────────────
def _row(nn, sc, out, age, pmin, pmed, instock):
    return {
        "norm_name": nn, "store_count": sc, "out_ratio": out,
        "price_min": pmin, "price_median": pmed, "price_max": pmed,
        "instock_price_min": instock, "rating_avg": None, "rating_count": 0,
        "in_our_catalog": 0, "data_age_days": age, "last_seen": "2026-07-08",
    }


def test_staleness_blocks_price_not_product_and_keeps_order():
    fresh_strong = _row("strong", 10, 0.9, age=0.0, pmin=100, pmed=110, instock=100)
    stale_weak = _row("weak", 3, 0.0, age=9.0, pmin=100, pmed=110, instock=100)
    ranked = rank_opportunities([stale_weak, fresh_strong])  # ترتيب مقلوب عمداً

    # المنتج القديم لا يُخفى
    assert {o.norm_name for o in ranked} == {"strong", "weak"}
    # الترتيب بالدرجة فقط (القوي أولاً) — القِدَم لا يؤثّر
    assert ranked[0].norm_name == "strong"

    by = {o.norm_name: o for o in ranked}
    assert by["strong"].suggested_price is not None and by["strong"].stale_reason is None
    assert by["weak"].suggested_price is None and by["weak"].stale_reason == "stale"


def test_staleness_does_not_change_score():
    """نفس الصف بعمرين مختلفين ⇒ نفس الدرجة (القِدَم خارج الدرجة تماماً)."""
    young = _row("x", 6, 0.5, age=0.0, pmin=100, pmed=110, instock=100)
    old = _row("x", 6, 0.5, age=99.0, pmin=100, pmed=110, instock=100)
    assert rank_opportunities([young])[0].score == rank_opportunities([old])[0].score
