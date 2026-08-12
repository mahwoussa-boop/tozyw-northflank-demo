# -*- coding: utf-8 -*-
"""توفّر المنافس الحقيقي: _parse_hit يقرأ status="out" (نافد) لا purchasable وحده.

المصدر يُبقي purchasable=true حتى للنافد؛ الإشارة الحقيقية status="out".
غياب status ⇒ السلوك القديم (purchasable) — لا تغيير لمتاجر لا ترسله.
"""
from engines.mahally_scraper import MahallyScraper

_S = MahallyScraper(db_path=None)
_N = "عطر اختبار"


def _availability(extra):
    hit = {"name": _N, "price": 100, "image": "https://c/m.jpg"}
    hit.update(extra)
    return _S._parse_hit(hit, "T")["availability"]


def test_status_out_means_unavailable_even_if_purchasable():
    """status='out' مع purchasable=true ⇒ نافد (False)."""
    assert _availability({"status": "out", "purchasable": True}) is False


def test_no_status_falls_back_to_purchasable_old_behavior():
    """بلا status مع purchasable=true ⇒ متوفر (True) — السلوك القديم حرفياً."""
    assert _availability({"purchasable": True}) is True


def test_status_sale_means_available():
    """status='sale' مع purchasable=true ⇒ متوفر (True)."""
    assert _availability({"status": "sale", "purchasable": True}) is True
