# -*- coding: utf-8 -*-
"""حارس رابط المنافس (خطوة ج): لا يُكتب رابط الأرخص عند فشل الفحص الهيكلي.

يستدعي engine._row مباشرةً (دالة نقية بلا قاعدة) في مسار المطابقة المؤكّدة،
ثم يتحقّق أن التعارض الهيكلي (حجم مختلف) يُفرّغ رابط_المنافس (= "" مثل مساري
لا-مطابقة القائمين)، بينما التطابق يكتب الرابط كما في السلوك القديم تماماً.
"""
from engines.engine import _row

_OUR = "عطر ديور سوفاج او دو تواليت 100مل"


def _match_row(comp_name, url):
    """صف مطابقة مؤكّدة (score ≥ 85، src=gemini) لمنافس واحد بالاسم/الرابط المعطى."""
    return _row(
        _OUR, 500.0, "OUR1", "ديور", 100, "او دو تواليت", "رجالي",
        best={"name": comp_name, "price": 400.0, "score": 92,
              "product_url": url, "competitor": "متجرأ"},
        src="gemini",
    )


def test_link_blanked_on_structural_conflict():
    """50مل مقابل 100مل ⇒ رابط_المنافس فارغ + قرار «تحت المراجعة»."""
    row = _match_row("عطر ديور سوفاج او دو تواليت 50مل", "http://comp/x50")
    assert row["رابط_المنافس"] == ""
    assert "تحت المراجعة" in row["القرار"]


def test_link_written_on_structural_match():
    """تطابق هيكلي (100مل=100مل، نفس الماركة) ⇒ الرابط يُكتب كالسلوك القديم."""
    row = _match_row("عطر ديور سوفاج او دو تواليت 100مل", "http://comp/ok100")
    assert row["رابط_المنافس"] == "http://comp/ok100"


def test_link_written_when_check_not_applicable():
    """مسار لا يعمل فيه الفحص الهيكلي (override=True) + تعارض 50/100 ⇒
    الرابط يُكتب كما في master والقرار لا يحتوي «تحت المراجعة»."""
    row = _row(
        _OUR, 500.0, "OUR1", "ديور", 100, "او دو تواليت", "رجالي",
        best={"name": "عطر ديور سوفاج او دو تواليت 50مل", "price": 400.0, "score": 92,
              "product_url": "http://comp/ov50", "competitor": "متجرأ"},
        override="🟢 سعر أقل", src="gemini",
    )
    assert row["رابط_المنافس"] == "http://comp/ov50"
    assert "تحت المراجعة" not in row["القرار"]
