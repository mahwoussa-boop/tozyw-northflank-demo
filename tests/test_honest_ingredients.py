"""tests/test_honest_ingredients.py — صدق المكوّنات في وصف العناية/المكياج.

يثبّت أن قالب salla_shamel_export لم يعد يلفّق أربعة مكوّنات (الأرغان/الكيراتين/
البيوتين/زبدة الشيا) لمنتجات العناية بلا بيانات، وأن قسم «المكونات الفعّالة»:
- يُسقَط كلياً عند غياب مكوّنات حقيقية.
- يظهر بالمكوّن الحقيقي حين يُمرَّر فعلاً.
- لا يمسّ فرع العطور («المكونات العطرية» يعمل كما هو — حارس).
"""
from utils.salla_shamel_export import generate_salla_html_description as _gen

_FABRICATED = ("زيت الأرغان", "الكيراتين", "البيوتين", "زبدة الشيا")


def test_care_without_ingredients_drops_section_and_fabrications():
    html = _gen("شامبو كيراتين للشعر", brand_name="لوريال", product_type="care")
    assert "المكونات الفعّالة" not in html          # العنوان مُسقَط كلياً
    assert not any(name in html for name in _FABRICATED)  # لا أسماء ملفّقة


def test_care_with_real_ingredients_shows_them():
    html = _gen("كريم مرطب", brand_name="نيفيا", product_type="care",
                ingredients="جليسرين، ألوفيرا")
    assert "المكونات الفعّالة" in html and "جليسرين" in html
    assert not any(name in html for name in _FABRICATED)  # حقيقي فقط، بلا تلفيق


def test_perfume_ingredients_section_still_works():
    html = _gen("عطر ديور سوفاج", brand_name="Dior", product_type="perfume",
                top_notes="برغموت", heart_notes="لافندر", base_notes="عنبر")
    assert "المكونات العطرية" in html               # فرع العطور سليم (حارس)
    assert not any(name in html for name in _FABRICATED)
