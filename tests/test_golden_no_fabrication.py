# -*- coding: utf-8 -*-
"""لا اختلاق في وصف المنتج المُرسَل لسلة (engines/golden_template.py).

``build_golden_description`` هو الوصف الفعليّ المُرسَل عبر Make (utils/make_helper.py
:_apply_golden_description). كانت ثلاث قيم تُلفَّق عند الجهل فتصل الزبون كحقيقة:
الحجم «100 مل»، التركيز «EDP»، والماركة «ماركة فاخرة»/«ماركة عالمية».

قياس حيّ على الكتالوج (2026-07-25، 11,566 منتجاً) قبل الإصلاح: 2,196 منتجاً بلا
حجم بالمل — **منها 520 حجمها معلوم بوحدة أخرى** (بودرة 200 جم، كريم 150جم، بخور
30جرام) فكان يُكتب فوقه «100 مل»، و6,454 بلا أي إشارة تركيز.

القاعدة المعتمدة (تعميم قرار الجنس، الجلسة 18): **المجهول يُسقَط سطره ولا يُلفَّق.**
"""
from engines.golden_template import (
    _extract_brand,
    _extract_concentration,
    _extract_size,
    build_golden_description,
)


def _text(product: dict) -> str:
    import re
    return re.sub("<[^>]+>", " ", build_golden_description(product))


# ── المُستخرِجات: المجهول = "" ─────────────────────────────────────────────
def test_unknown_size_is_empty_not_fabricated_100ml():
    assert _extract_size("مجموعة هدايا كايالي فيري فانيلا") == ""
    assert _extract_size("") == ""


def test_known_size_in_ml_is_preserved():
    assert _extract_size("عطر ديور سوفاج 100 مل للرجال") == "100 مل"
    assert _extract_size("Dior Sauvage 50ml") == "50 مل"


def test_size_in_non_liquid_units_is_read_not_overwritten():
    """الـ520 الأخطر: الحجم معلوم بالجرام والمنتج ليس سائلاً أصلاً."""
    assert _extract_size("كريم الجسم شانيل ان فايف 150جم") == "150 جم"
    assert _extract_size("بودرة معطرة صندوق استي لودر يوث ديو 200 جم") == "200 جم"
    assert _extract_size("بخور الذهبي بانافع للعود 30جرام") == "30 جم"


def test_unknown_concentration_is_empty_not_fabricated_edp():
    assert _extract_concentration("عطر لطافة مجموعة المسك الشرقي") == ""


def test_known_concentration_is_preserved():
    assert "EDT" in _extract_concentration("عطر ديور سوفاج أو دو تواليت 100 مل")
    assert "EDP" in _extract_concentration("عطر شانيل بلو أو دو بارفيوم 150 مل")


def test_unknown_brand_is_empty_not_fabricated():
    assert _extract_brand("عطر مجهول الماركة", None) == ""
    assert _extract_brand("أي اسم", "شانيل") == "شانيل"  # العمود الصريح يُحترم


# ── الوصف الكامل: لا تظهر القيمة الملفَّقة ولا سطر يتيم ────────────────────
def test_description_omits_lines_for_unknown_specs():
    txt = _text({"اسم المنتج": "مجموعة هدايا كايالي فيري فانيلا"})
    assert "100 مل" not in txt
    assert "ماركة فاخرة" not in txt and "ماركة عالمية" not in txt
    assert "الحجم:" not in txt
    assert "التركيز:" not in txt


def test_care_product_shows_its_real_gram_size():
    txt = _text({"اسم المنتج": "كريم الجسم شانيل ان فايف 150جم"})
    assert "150 جم" in txt
    assert "100 مل" not in txt


def test_known_specs_still_render():
    """حارس ضد الإفراط: المنتج كامل المعلومات لا يفقد شيئاً."""
    txt = _text({"اسم المنتج": "عطر ديور سوفاج أو دو تواليت 100 مل للرجال",
                 "الماركة": "ديور"})
    assert "100 مل" in txt
    assert "ديور" in txt
    assert "EDT" in txt


def test_no_orphan_separator_when_brand_missing():
    """الماركة الفارغة كانت تترك «كلمات البحث: اسم | » بفاصل يتيم."""
    txt = _text({"اسم المنتج": "منتج بلا ماركة معروفة 75 مل"})
    assert "| |" not in txt
    assert "من  —" not in txt  # نصّ العناية كان ينكسر بفراغ بين «من» و«—»
