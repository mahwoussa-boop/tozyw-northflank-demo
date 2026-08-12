"""tests/test_make_brand.py — ضمان «الماركة لا تُرسَل فارغة أبداً» (إصلاح #3).

يختبر نقطة الاختناق النقية ``utils.make_helper._brand_name`` التي يمرّ بها كلا
مسارَي الإرسال (``send_new_products`` / ``send_missing_products``): تُسقط القيم
النائبة («غير متوفر»/nan)، وتتدرّج لاستخراج الماركة من اسم المنتج كملاذ أخير.
"""
from utils.make_helper import _brand_name


def test_explicit_real_brand_kept() -> None:
    assert _brand_name({"الماركة": "Lattafa"}) == "Lattafa"
    assert _brand_name({"brand": "Armaf"}) == "Armaf"


def test_placeholder_brand_recovered_from_name() -> None:
    # «غير متوفر» قيمة نائبة لا تُرسَل كماركة — تُستخرج من اسم المنتج بدلاً منها.
    out = _brand_name({"الماركة": "غير متوفر", "أسم المنتج": "Lattafa Asad 100ml"})
    assert out == "Lattafa"
    assert out.lower() not in ("غير متوفر", "nan", "")


def test_empty_brand_recovered_from_name() -> None:
    assert _brand_name({"منتج_المنافس": "Armaf Club de Nuit"}) == "Armaf"


def test_unknown_brand_returns_empty_not_fabricated() -> None:
    # ماركة غير معروفة لا تُختلَق — تبقى فارغة (بوابة الجودة تُسقط المنتج لاحقاً).
    out = _brand_name({"أسم المنتج": "منتج مجهول بلا ماركة معروفة"})
    assert out == ""


def test_nan_brand_treated_as_blank() -> None:
    assert _brand_name({"الماركة": "nan", "أسم المنتج": "Dior Sauvage"}) == "Dior"
