"""tests/test_dict_series_parity.py — تكافؤ dict/Series لدوال قراءة الصف في المحرّك.

الحلقة الرئيسة في ``engines/engine.py`` (السطر ~2809) حُوّلت لأداءٍ إلى
``our_df.to_dict('records')`` ⇒ صار ``row`` من نوع ``dict``. أربع دوال قراءة صف
(``_pid``, ``_price``, ``_first_product_page_url_from_row``,
``_first_image_url_from_row``) كانت تفترض ``row.index`` (خاصية Series فقط)
فتنهار على dict بـ ``AttributeError: 'dict' object has no attribute 'index'``.

هذا الاختبار يستدعي كل دالة بمدخلين لهما **نفس المحتوى** — ``dict`` و
``pandas.Series`` — ويؤكّد **تطابق الناتج**. قبل الإصلاح يفشل على مدخل الـdict
(إثبات إعادة الإنتاج)؛ بعد الإصلاح يمرّ. لا SQL/شبكة/AI (دوال نقيّة).
"""
import pandas as pd

from engines.engine import (
    _first_image_url_from_row,
    _first_product_page_url_from_row,
    _pid,
    _price,
)

# صفّ تمثيلي بنفس ترتيب المفاتيح للنوعين (ترتيب الإدراج = ترتيب الفهرس)
_ROW = {
    "معرف_المنتج": "12345.0",
    "السعر": "45.50",
    "رابط_المنتج": "http://example.com/product/1",
    "صورة_المنتج": "http://example.com/pic.jpg",
    "المنتج": "عطر تجريبي",
}


def _as_dict():
    return dict(_ROW)


def _as_series():
    return pd.Series(_ROW)


def test_pid_dict_series_parity():
    d, s = _pid(_as_dict(), "معرف_المنتج"), _pid(_as_series(), "معرف_المنتج")
    assert d == s == "12345"


def test_price_dict_series_parity():
    d, s = _price(_as_dict()), _price(_as_series())
    assert d == s == 45.5


def test_product_page_url_dict_series_parity():
    d = _first_product_page_url_from_row(_as_dict())
    s = _first_product_page_url_from_row(_as_series())
    assert d == s == "http://example.com/product/1"


def test_image_url_dict_series_parity():
    d = _first_image_url_from_row(_as_dict())
    s = _first_image_url_from_row(_as_series())
    assert d == s == "http://example.com/pic.jpg"


def test_pid_missing_col_dict_series_parity():
    # عمود معرف غائب ⇒ "" للنوعين (لا انهيار على dict)
    assert _pid(_as_dict(), "عمود_غير_موجود") == _pid(_as_series(), "عمود_غير_موجود") == ""
