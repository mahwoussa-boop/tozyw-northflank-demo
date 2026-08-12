"""tests/test_golden_notes.py — القالب الذهبي: الهرم يظهر بالنفحات، ولا اختلاق بدونها.

``build_golden_description`` هو الوصف الفعليّ المُرسَل لسلة عبر Make (make_helper /
product_gate). حتميّ (بلا API): يُدرِج نفحات الهرم حين تتوفّر (top/heart/base)،
ويتدرّج للنسخة العامة **بلا اختلاق نفحات** حين تغيب — has_notes gating.

حارس انحدار للتشخيص: «بلا هرم» سببه غياب جلب النفحات (fetch_fragrantica_info)،
لا بُقّ قالب — فالقالب هنا مُثبَّت: موجودة⇒تظهر، غائبة⇒لا تُختلَق.
"""
from engines.golden_template import build_golden_description

_BASE = {
    "المنتج": "عطر ديور سوفاج",
    "الماركة": "Dior",
    "الجنس": "رجالي",
    "الحجم": "100",
    "السعر": "450",
    "تصنيف_المنتج": "عطور رجالية",
}
_NOTES = ("برغموت", "لافندر", "عنبر")


def _with_notes():
    return build_golden_description({
        **_BASE,
        "top_notes": "برغموت, فلفل",
        "heart_notes": "لافندر",
        "base_notes": "عنبر, أرز",
        "العائلة_العطرية": "خشبي",
    })


def test_pyramid_notes_appear_when_present():
    html = _with_notes()
    for note in _NOTES:
        assert note in html  # الهرم يظهر بنفحاته الفعلية


def test_no_fabricated_notes_when_absent():
    html = build_golden_description(dict(_BASE))  # بلا top/heart/base
    for fabricated in _NOTES:
        assert fabricated not in html  # لا اختلاق نفحات (صدق)


def test_keywords_always_present():
    # الكلمات (SEO) جزء دائم من القالب الذهبي — بنفحات وبدونها.
    assert "كلمات البحث" in build_golden_description(dict(_BASE))
    assert "كلمات البحث" in _with_notes()


def test_notes_version_is_richer_than_general():
    # نسخة النفحات أطول (قسم الهرم/البطاقة الإضافي) — تأكيد التدرّج has_notes.
    assert len(_with_notes()) > len(build_golden_description(dict(_BASE)))
