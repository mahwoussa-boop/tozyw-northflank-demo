"""م2 — التوفّر والحداثة يصلان فعلاً إلى صفوف الأقسام.

قبل هذا الإصلاح كانت إطارات القرار تحمل 24 عموداً **ليس فيها توفّر ولا تاريخ
كشط** (صفر ورود لـ``availability`` في ``engines/engine.py``)، فيُبنى قرار السعر
على عرضِ منافسٍ قد يكون نافداً منذ أسابيع بلا أن يظهر ذلك للمالك.

الاختبار يحقن خريطتَي الخدمة المشتركة مباشرة (لا قاعدة، لا شبكة) ويتحقّق أن
الصفّ يحمل العمودين بالقيمة الصحيحة — ومن الوسم لا من النصّ الحرفي.
"""
from __future__ import annotations

import pytest

from engines import engine as eng
from services import competitor_availability as ca

_URL_OUT = "https://mahally.com/products/1/oos"
_URL_IN = "https://mahally.com/products/1/live"


@pytest.fixture
def _stub_availability(monkeypatch):
    """يثبّت الخريطتين على مستوى الخدمة — المصدر الوحيد الذي يقرأه المحرّك."""
    monkeypatch.setattr(ca, "oos_links", lambda: frozenset({_URL_OUT}))
    monkeypatch.setattr(ca, "data_ages", lambda: {_URL_OUT: 11.5, _URL_IN: 0.5})


def _cand(url: str, price: float) -> dict:
    return {
        "name": "عطر تجريبي 100 مل", "score": 95.0, "price": price,
        "product_id": "c1", "brand": "شانيل", "size": 100, "type": "edp",
        "gender": "", "competitor": "متجر أ", "product_url": url,
    }


def test_row_carries_availability_and_age_of_reference(_stub_availability):
    """المرجع نافد ⇒ العمودان يقولانها صراحةً (لا صمت)."""
    row = eng._row(
        "عطر تجريبي 100 مل", 300.0, "p1", "شانيل", 100, "edp", "",
        best=_cand(_URL_OUT, 250.0), all_cands=[_cand(_URL_OUT, 250.0)],
    )
    assert row["توفر_المنافس"] == ca.AVAIL_OUT
    assert row["عمر_بيانات_المنافس_أيام"] == pytest.approx(11.5)


def test_row_marks_available_reference_as_in_stock(_stub_availability):
    row = eng._row(
        "عطر تجريبي 100 مل", 300.0, "p1", "شانيل", 100, "edp", "",
        best=_cand(_URL_IN, 250.0), all_cands=[_cand(_URL_IN, 250.0)],
    )
    assert row["توفر_المنافس"] == ca.AVAIL_IN
    assert row["عمر_بيانات_المنافس_أيام"] == pytest.approx(0.5)


def test_rows_without_a_reference_claim_nothing(_stub_availability):
    """بلا منافس مرجعي: «غير معروف» لا «متوفر» — لا نَدَّعي ما لم نقسه."""
    missing = eng._row("عطر بلا منافس", 300.0, "p2", "شانيل", 100, "edp", "", best=None)
    assert missing["توفر_المنافس"] == ca.AVAIL_UNKNOWN
    assert missing["عمر_بيانات_المنافس_أيام"] is None

    excluded = eng._excluded_match_row(
        "عطر مستبعد", 300.0, "p3", "شانيل", 100, "edp", "", score=12.0,
    )
    assert excluded["توفر_المنافس"] == ca.AVAIL_UNKNOWN
    assert excluded["عمر_بيانات_المنافس_أيام"] is None


def test_all_row_builders_share_one_column_schema(_stub_availability):
    """الأقسام تُجمَّع في إطار واحد ⇒ اختلاف الأعمدة يولّد NaN صامتاً."""
    matched = eng._row(
        "عطر تجريبي 100 مل", 300.0, "p1", "شانيل", 100, "edp", "",
        best=_cand(_URL_IN, 250.0), all_cands=[_cand(_URL_IN, 250.0)],
    )
    missing = eng._row("عطر بلا منافس", 300.0, "p2", "شانيل", 100, "edp", "", best=None)
    excluded = eng._excluded_match_row(
        "عطر مستبعد", 300.0, "p3", "شانيل", 100, "edp", "", score=12.0,
    )
    assert set(matched) == set(missing) == set(excluded)
    for col in ("توفر_المنافس", "عمر_بيانات_المنافس_أيام"):
        assert col in matched


def test_lookup_is_fail_open_when_maps_are_empty(monkeypatch):
    """عطل تقني (قاعدة غائبة) ⇒ «غير معروف»، لا حجب ولا ادّعاء توفّر."""
    monkeypatch.setattr(ca, "oos_links", frozenset)
    monkeypatch.setattr(ca, "data_ages", dict)
    assert ca.lookup(_URL_IN) == (ca.AVAIL_UNKNOWN, None)
    assert ca.lookup("") == (ca.AVAIL_UNKNOWN, None)
