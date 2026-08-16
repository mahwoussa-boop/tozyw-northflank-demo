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


def test_low_confidence_offer_stays_visible_but_never_controls_price(_avail):
    """عرض 70% أرخص يبقى في الشريط، لكنه لا يحدد قرار السعر عند وجود 95%."""
    high = _cand(_LIVE, 120.0, "مرجع مؤكد")
    high["score"] = 95.0
    low = _cand(_LIVE2, 50.0, "مرجع رمادي")
    low["score"] = 70.0

    row = eng._row("عطر تجريبي 100 مل", 100.0, "p1", "شانيل", 100, "edp", "",
                   best=high, all_cands=[high, low])

    assert row["سعر_المنافس"] == 120.0
    assert row["نسبة_التطابق"] == 95.0
    assert row["القرار"] == "🟢 سعر أقل"
    assert row["ملاحظة_المرجع"] == "عروض منخفضة الثقة مستبعدة من مرجع السعر"
    assert _LIVE2 in {c["product_url"] for c in row["جميع_المنافسين"]}


def test_only_low_confidence_ai_match_is_forced_to_review(_avail):
    """اختيار AI لمرشح رمادي لا يحوّله إلى توصية سعر آلية."""
    low = _cand(_LIVE, 50.0, "مرشح AI رمادي")
    low["score"] = 72.0

    row = eng._row("عطر تجريبي 100 مل", 100.0, "p1", "شانيل", 100, "edp", "",
                   best=low, src="gemini", all_cands=[low])

    assert row["سعر_المنافس"] == 50.0
    assert row["القرار"].startswith("⚠️ تحت المراجعة — مرجع منخفض الثقة")
    assert row["ملاحظة_المرجع"] == "لا يوجد مرجع سعري مؤكد — تحت المراجعة"


def test_high_fuzzy_flanker_is_not_a_pricing_reference(_avail):
    """تشابه ≥85 لا يكفي إذا كان خط المنتج مختلفاً؛ النتيجة مراجعة لا تسعير."""
    wrong_line = {
        "name": "عطر فرنش افينيو اكسيس او دو بارفيوم 100مل",
        "score": 92.0,
        "price": 90.0,
        "product_id": "axis-100",
        "brand": "فرنش افينيو",
        "size": 100,
        "type": "EDP",
        "gender": "",
        "competitor": "مرجع فلانكر مختلف",
        "product_url": _LIVE,
    }
    row = eng._row("عطر فرنش افينيو شايوس او دو بارفيوم 100مل", 129.0, "p1",
                   "فرنش افينيو", 100, "EDP", "", best=wrong_line,
                   all_cands=[wrong_line])

    assert row["القرار"].startswith("⚠️ تحت المراجعة — مرجع منخفض الثقة")
    assert row["ملاحظة_المرجع"] == "لا يوجد مرجع سعري مؤكد — تحت المراجعة"


def test_confirmed_same_line_offer_remains_eligible_for_pricing(_avail):
    """الحارس لا يمنع مرجعاً مؤكدًا مطابقاً في الاسم/الخط/الحجم/التركيز."""
    exact = {
        "name": "عطر فرنش افينيو شايوس او دو بارفيوم 100مل",
        "score": 96.0,
        "price": 110.0,
        "product_id": "chaos-100",
        "brand": "فرنش افينيو",
        "size": 100,
        "type": "EDP",
        "gender": "",
        "competitor": "مرجع مطابق",
        "product_url": _LIVE,
    }
    row = eng._row("عطر فرنش افينيو شايوس او دو بارفيوم 100مل", 129.0, "p1",
                   "فرنش افينيو", 100, "EDP", "", best=exact, all_cands=[exact])

    assert row["سعر_المنافس"] == 110.0
    assert row["القرار"] == "🔴 سعر أعلى"
    assert row["ملاحظة_المرجع"] == ""


def _named_cand(name: str, score: float = 96.0, price: float = 110.0) -> dict:
    return {
        "name": name, "score": score, "price": price,
        "product_id": "named-ref", "brand": "دو", "size": 100, "type": "EDT",
        "gender": "", "competitor": "مرجع اختبار", "product_url": _LIVE,
    }


def test_different_molecule_version_is_not_a_pricing_reference(_avail):
    """Molecule 03 يجب ألا يسعّر من Escentric 03 رغم التشابه النصي العالي."""
    wrong = _named_cand("عطر إسكينتريك موليكيولز إسكينتريك 03 او دو تواليت للجنسين 100 مل", 94.8)
    row = eng._row(
        "عطر إسكينتريك موليكيولز موليكيول 03 أو دو تواليت 100 مل",
        129.0, "p-molecule-03", "دو", 100, "EDT", "", best=wrong, all_cands=[wrong],
    )
    assert row["القرار"].startswith("⚠️ تحت المراجعة")
    assert row["ملاحظة_المرجع"] == "لا يوجد مرجع سعري مؤكد — تحت المراجعة"


def test_different_escentric_version_is_not_a_pricing_reference(_avail):
    """Escentric 05 يجب ألا يسعّر من Escentric 03."""
    wrong = _named_cand("عطر إسكينتريك موليكيولز إسكينتريك 03 او دو تواليت للجنسين 100 مل", 90.0)
    row = eng._row(
        "عطر اسنترك موليكيولز اسنتريك 05 أو دو تواليت 100 مل",
        129.0, "p-escentric-05", "دو", 100, "EDT", "", best=wrong, all_cands=[wrong],
    )
    assert row["القرار"].startswith("⚠️ تحت المراجعة")


def test_missing_competitor_product_line_is_not_a_pricing_reference(_avail):
    """مرجع بلا خط منتج لا يمر تلقائياً لمنتج ذي خط معروف."""
    wrong = _named_cand("عطر جيرلان هوم - 100 مل", 88.6, 90.0)
    wrong["brand"] = "Guerlain"
    row = eng._row(
        "عطر جيرلان لو هوم أيديال أو دو كولون 100مل",
        129.0, "p-guerlain", "Guerlain", 100, "EDC", "", best=wrong, all_cands=[wrong],
    )
    assert row["القرار"].startswith("⚠️ تحت المراجعة")


def test_same_escentric_version_remains_eligible(_avail):
    """Escentric 03 المطابق يبقى مؤهلاً بعد تشديد البوابة."""
    exact = _named_cand("عطر إسكينتريك موليكيولز إسكينتريك 03 او دو تواليت للجنسين 100 مل", 96.0, 110.0)
    row = eng._row(
        "عطر إسكينتريك موليكيولز إسكينتريك 03 أو دو تواليت 100 مل",
        129.0, "p-escentric-03", "دو", 100, "EDT", "", best=exact, all_cands=[exact],
    )
    assert row["سعر_المنافس"] == 110.0
    assert row["القرار"] == "🔴 سعر أعلى"
