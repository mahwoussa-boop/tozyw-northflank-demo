"""tests/test_scraper_selectors.py — تدقيق مُحدِّدات كشط المتاجر الكبرى.

القاعدة الذهبية: ممنوع النسخ الأعمى. هذه الاختبارات تتحقّق أن منطق الاستخراج القديم
(JSON-LD / OG-meta — وهو «المُحدِّدات» الفعلية المستقلّة عن تصميم المتجر) ما زال يجلب
الاسم/السعر/الرابط/الصورة/**حالة المخزون** من بنية schema.org التي تنشرها متاجر سلة/زد/
ماجنتو (قولدن سنت، سيفورا، نيش، وجوه…). عيّنات HTML ثابتة ⇒ حتمية بلا شبكة (صالحة لـ CI).

تثبت أيضاً **الحلقة الكاملة**: الكاشط يلتقط نفاد المخزون ⇒ خطّاف الحرجية (المهمة 2)
يرفع المنتج تلقائياً إلى «🔴 حرج».
"""
from __future__ import annotations

import pytest

from engines.json_ld_extractor import _norm_availability, extract
from ui.components.criticality import (
    CRITICAL,
    competitor_out_of_stock,
    criticality_of_row,
)


def _page(*scripts: str, head_meta: str = "") -> str:
    """يلفّ مقاطع HTML في صفحة كاملة (head للـ meta، body للـ JSON-LD)."""
    return f"<html><head>{head_meta}</head><body>{''.join(scripts)}</body></html>"


# ── عيّنات تحاكي ما تنشره المتاجر فعلاً (schema.org Product) ──
_JSONLD_INSTOCK = """<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product",
 "name":"عطر بلو دو شانيل 100 مل","sku":"CH-BDC-100",
 "image":"https://cdn.salla.sa/abc/blue.jpg",
 "brand":{"@type":"Brand","name":"Chanel"},
 "offers":{"@type":"Offer","price":"545.00","priceCurrency":"SAR",
           "availability":"https://schema.org/InStock"}}
</script>"""

_JSONLD_OOS = """<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"عطر نفد",
 "offers":{"@type":"Offer","price":"299","priceCurrency":"SAR",
           "availability":"http://schema.org/OutOfStock"}}</script>"""

# سلة/قولدن سنت يلفّان أحياناً المنتج داخل @graph + قائمة صور.
_JSONLD_GRAPH = """<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
  {"@type":"WebSite","name":"Goldenscent"},
  {"@type":"Product","name":"Sauvage EDP","sku":"DIOR-SAU",
   "image":["https://img/g1.jpg","https://img/g2.jpg"],
   "offers":{"@type":"Offer","price":"420","priceCurrency":"SAR",
             "availability":"InStock"}}]}</script>"""

# عملة غير سعودية ⇒ تُتجاهَل (قائمة الحظر USD/EUR/AED…).
_JSONLD_USD = """<script type="application/ld+json">
{"@type":"Product","name":"X","offers":{"price":"100","priceCurrency":"USD",
 "availability":"InStock"}}</script>"""

# متجر لا ينشر JSON-LD ⇒ fallback عبر OG/product meta.
_OG_META = (
    '<meta property="og:title" content="عطر وجوه">'
    '<meta property="og:image" content="https://faces/og.jpg">'
    '<meta property="product:price:amount" content="350">'
    '<meta property="product:price:currency" content="SAR">'
    '<meta property="product:availability" content="out of stock">'
)


def test_jsonld_instock_full_fields() -> None:
    d = extract(_page(_JSONLD_INSTOCK))
    assert d["name"] == "عطر بلو دو شانيل 100 مل"
    assert d["price"] == 545.0
    assert d["image"] == "https://cdn.salla.sa/abc/blue.jpg"
    assert d["sku"] == "CH-BDC-100"
    assert d["brand"] == "Chanel"
    assert d["availability"] == "InStock"
    assert d["currency"] == "SAR" and d["source"] == "json-ld"


def test_jsonld_out_of_stock_captured() -> None:
    # الإصلاح الجوهري: حالة المخزون كانت تُفقد في المسار الأساسي — الآن تُلتقط.
    d = extract(_page(_JSONLD_OOS))
    assert d["price"] == 299.0
    assert d["availability"] == "OutOfStock"


def test_jsonld_graph_array_and_image_list() -> None:
    d = extract(_page(_JSONLD_GRAPH))
    assert d["name"] == "Sauvage EDP" and d["price"] == 420.0
    assert d["image"] == "https://img/g1.jpg"   # أول صورة من القائمة
    assert d["availability"] == "InStock"


def test_non_sar_offer_skipped() -> None:
    assert extract(_page(_JSONLD_USD)) == {}


def test_og_meta_fallback_with_availability() -> None:
    d = extract(_page(head_meta=_OG_META))
    assert d["price"] == 350.0 and d["source"] == "og-meta"
    assert d["name"] == "عطر وجوه"
    assert "out of stock" in d["availability"].lower()


def test_empty_or_blank_html() -> None:
    assert extract("") == {}
    assert extract("<html></html>") == {}


def test_norm_availability_variants() -> None:
    assert _norm_availability("https://schema.org/InStock") == "InStock"
    assert _norm_availability("http://schema.org/OutOfStock") == "OutOfStock"
    assert _norm_availability("InStock") == "InStock"
    assert _norm_availability("") == "" and _norm_availability(None) == ""


def test_extracted_stock_feeds_criticality() -> None:
    # الحلقة الكاملة: نفاد مخزون المنافس (من الكشط) ⇒ «🔴 حرج» تلقائياً.
    d = extract(_page(_JSONLD_OOS))
    row = {"availability": d["availability"], "سعر_المنافس": 100, "السعر": 100}
    assert competitor_out_of_stock(row) is True
    assert criticality_of_row(row) == CRITICAL


@pytest.mark.parametrize("avail,is_oos", [
    ("InStock", False), ("OutOfStock", True), ("SoldOut", True),
])
def test_async_extract_product_propagates_availability(avail: str, is_oos: bool) -> None:
    # تكامل: المسار الأساسي يمرّر حالة المخزون (بدل الافتراض «true») ⇒ تصل للحرجية.
    pytest.importorskip("aiohttp")  # محرك الكشط يتطلب aiohttp
    from engines.async_scraper import extract_product

    row = extract_product(
        {"name": "عطر", "price": 100, "url": "https://store.sa/products/x",
         "available": avail},
        "https://store.sa",
    )
    assert row["availability"] == avail
    assert competitor_out_of_stock({"availability": row["availability"]}) is is_oos
