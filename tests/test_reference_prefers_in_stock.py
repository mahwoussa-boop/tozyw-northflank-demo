"""م3 — المرجع السعري هو الأرخص **المتوفّر** لا الأرخص مطلقاً.

قبل الإصلاح: ``engine._row`` يختار المرجع بـ``min(price)`` بلا أي فلترة توفّر،
فعرضٌ نافد منذ أسابيع يصير أساس قرار «سعر أعلى» ويضغط لخفض سعرنا أمام منافس
لا يبيع أصلاً. قياس ظلّ على اللقطة الحيّة: 1,456 صفّاً (20.7% من أقسام السعر)
مرجعها نافد، و417 صفّاً كان قسمه سيتغيّر — 293 منها تخرج من «سعر أعلى».

النافد **لا يُحذف**: يبقى في ``جميع_المنافسين`` (شارة «🔴 نفذت» في البطاقة)،
ويُكتب سبب صريح في ``ملاحظة_المرجع`` كي لا يرى المالك سعراً ليس الأرخص بلا تفسير.
"""
from __future__ import annotations

import pytest

from engines import engine as eng
from services import competitor_availability as ca

_OOS = "https://mahally.com/products/1/oos"
_LIVE = "https://mahally.com/products/1/live"
_LIVE2 = "https://mahally.com/products/1/live2"


@pytest.fixture
def _avail(monkeypatch):
    monkeypatch.setattr(ca, "oos_links", lambda: frozenset({_OOS}))
    monkeypatch.setattr(ca, "data_ages", lambda: {_OOS: 9.0, _LIVE: 1.0, _LIVE2: 1.0})


def _cand(url: str, price: float, store: str) -> dict:
    return {
        "name": "عطر تجريبي 100 مل", "score": 95.0, "price": price,
        "product_id": f"c-{store}", "brand": "شانيل", "size": 100, "type": "edp",
        "gender": "", "competitor": store, "product_url": url,
    }


def test_cheaper_out_of_stock_offer_is_not_the_reference(_avail):
    """الأرخص نافد (250) والأغلى متاح (290) ⇒ المرجع 290."""
    cands = [_cand(_OOS, 250.0, "متجر نافد"), _cand(_LIVE, 290.0, "متجر متاح")]
    row = eng._row("عطر تجريبي 100 مل", 300.0, "p1", "شانيل", 100, "edp", "",
                   best=cands[0], all_cands=cands)

    assert row["سعر_المنافس"] == 290.0
    assert row["المنافس"] == "متجر متاح"
    assert row["توفر_المنافس"] == ca.AVAIL_IN
    assert row["ملاحظة_المرجع"] == "المرجع الأرخص نافد — قورن بأرخص متوفّر"


def test_out_of_stock_offer_stays_visible(_avail):
    """الحجب عن المرجعية ليس حذفاً — النافد يبقى معروضاً في قائمة المنافسين."""
    cands = [_cand(_OOS, 250.0, "متجر نافد"), _cand(_LIVE, 290.0, "متجر متاح")]
    row = eng._row("عطر تجريبي 100 مل", 300.0, "p1", "شانيل", 100, "edp", "",
                   best=cands[0], all_cands=cands)

    urls = {c["product_url"] for c in row["جميع_المنافسين"]}
    assert _OOS in urls, "النافد اختفى — هذا فقدان رؤية لا إصلاح"
    assert row["عدد_المنافسين"] == 2


def test_decision_flips_off_price_raise_when_reference_is_phantom(_avail):
    """جوهر م3: «سعر أعلى» أمام منافس نافد ⇒ «موافق» أمام المتاح الحقيقي."""
    cheap_oos = [_cand(_OOS, 250.0, "نافد"), _cand(_LIVE, 299.0, "متاح")]
    row = eng._row("عطر تجريبي 100 مل", 300.0, "p1", "شانيل", 100, "edp", "",
                   best=cheap_oos[0], all_cands=cheap_oos)
    assert row["القرار"] == "✅ موافق"

    # الحالة المقابلة: لو كان الأرخص متاحاً فعلاً، القرار يبقى «سعر أعلى» كما كان
    both_live = [_cand(_LIVE, 250.0, "متاح أ"), _cand(_LIVE2, 299.0, "متاح ب")]
    row2 = eng._row("عطر تجريبي 100 مل", 300.0, "p1", "شانيل", 100, "edp", "",
                    best=both_live[0], all_cands=both_live)
    assert row2["سعر_المنافس"] == 250.0
    assert row2["القرار"] == "🔴 سعر أعلى"
    assert row2["ملاحظة_المرجع"] == ""


def test_all_offers_out_of_stock_keeps_cheapest_and_says_so(_avail):
    """كل المطابقين نافدون ⇒ لا نُفرغ الصفّ؛ نُبقي المقارنة ونُعلن أنها تاريخية."""
    cands = [_cand(_OOS, 250.0, "نافد")]
    row = eng._row("عطر تجريبي 100 مل", 300.0, "p1", "شانيل", 100, "edp", "",
                   best=cands[0], all_cands=cands)

    assert row["سعر_المنافس"] == 250.0
    assert row["توفر_المنافس"] == ca.AVAIL_OUT
    assert row["ملاحظة_المرجع"] == "كل العروض المطابقة نافدة — المقارنة تاريخية"


def test_unknown_availability_is_not_treated_as_out_of_stock(_avail):
    """رابط لا نعرف توفّره يبقى مؤهّلاً للمرجعية — fail-open لا حجب بالشكّ."""
    unknown = "https://mahally.com/products/1/unknown"
    cands = [_cand(unknown, 250.0, "مجهول"), _cand(_LIVE, 290.0, "متاح")]
    row = eng._row("عطر تجريبي 100 مل", 300.0, "p1", "شانيل", 100, "edp", "",
                   best=cands[0], all_cands=cands)
    assert row["سعر_المنافس"] == 250.0
    assert row["ملاحظة_المرجع"] == ""
