# -*- coding: utf-8 -*-
"""tests/test_comparison_gallery.py — معرض صور بطاقة المقارنة (وحدة ج3 خ2).

يثبّت:
- ``utils.data_helpers.all_image_urls_list`` الخالصة: تفكيك خلية صور متعددة
  (نمط first_image_url_string نفسه) لكن قائمة كاملة لا رابط واحد.
- ``CompIndex.search`` يضيف ``comp_images`` (قائمة) لكل مرشَّح — [] افتراضياً
  حين لا عمود معرض (كل الاستدعاءات القائمة بلا gallery_col تبقى تعمل حرفياً
  كالسابق)، ومملوءة حين يوجد العمود.
- **اختبار الانحدار الإلزامي:** نفس صفوف المدخلات بحضور/غياب gallery_col تُنتج
  نفس الدرجة/الترتيب/كل الحقول الأخرى حرفياً — إضافة المعرض عرض صرف لا يمسّ
  المطابقة (#PRESERVED_LOGIC).
"""
import pandas as pd

from engines.engine import (
    CompIndex,
    extract_brand,
    extract_gender,
    extract_size,
    extract_type,
    normalize,
)
from utils.data_helpers import all_image_urls_list


# ── الدالة الخالصة ───────────────────────────────────────────────────────────
def test_all_image_urls_list_empty_input():
    assert all_image_urls_list("") == []
    assert all_image_urls_list(None) == []
    assert all_image_urls_list("لا رابط هنا") == []


def test_all_image_urls_list_single_url():
    url = "https://cdn.example.com/a.jpg"
    assert all_image_urls_list(url) == [url]


def test_all_image_urls_list_multiple_comma_separated():
    urls = [
        "https://cdn.example.com/a.jpg",
        "https://cdn.example.com/b.png",
        "https://cdn.example.com/c.webp",
    ]
    cell = ",".join(urls)
    assert all_image_urls_list(cell) == urls


def test_all_image_urls_list_arabic_comma_separator():
    urls = ["https://cdn.example.com/a.jpg", "https://cdn.example.com/b.jpg"]
    cell = "،".join(urls)  # فاصلة عربية
    assert all_image_urls_list(cell) == urls


def test_all_image_urls_list_ignores_non_image_urls():
    # رابط بلا امتداد صورة معروف لا يُلتقط عبر finditer، والاحتياط أحادي فقط.
    cell = "https://example.com/page,https://cdn.example.com/real.jpg"
    assert all_image_urls_list(cell) == ["https://cdn.example.com/real.jpg"]


# ── CompIndex.search: comp_images (نفس المنتج/الظروف المُثبَتة في
#    test_matching_bridge.py::test_flanker_guard_matches_identical_twin_with_hamza) ──
_PRODUCT = "عطر لطافة أوراق الفضة أو دو برفيوم 100 مل"


def _rows_df(gallery_values=None):
    rows = [{"id": 1, "المنتج": _PRODUCT, "السعر": 63.25, "brand": "لطافة"}]
    df = pd.DataFrame(rows)
    if gallery_values is not None:
        df["image_urls"] = gallery_values
    return df


def _search_args(product):
    return (
        normalize(product), extract_brand(product), extract_size(product),
        extract_type(product), extract_gender(product),
    )


def test_comp_images_empty_by_default_no_gallery_col():
    df = _rows_df()
    idx = CompIndex(df, "المنتج", "id", "متجر الاختبار")  # نفس الاستدعاء القديم بلا gallery_col
    res = idx.search(*_search_args(_PRODUCT), our_pline="اوراق الفضه", top_n=6, our_price=69.0)
    assert len(res) == 1
    assert res[0]["comp_images"] == []


def test_comp_images_populated_when_gallery_col_present():
    gallery = "https://cdn.example.com/1.jpg,https://cdn.example.com/2.jpg"
    df = _rows_df(gallery_values=[gallery])
    idx = CompIndex(df, "المنتج", "id", "متجر الاختبار", gallery_col="image_urls")
    res = idx.search(*_search_args(_PRODUCT), our_pline="اوراق الفضه", top_n=6, our_price=69.0)
    assert len(res) == 1
    assert res[0]["comp_images"] == [
        "https://cdn.example.com/1.jpg", "https://cdn.example.com/2.jpg",
    ]


# ── اختبار الانحدار الإلزامي: الدرجة/الترتيب لا يتأثران بالمعرض ──────────────
def test_gallery_col_does_not_change_matching_score_or_order():
    df_no_gallery = _rows_df()
    df_with_gallery = _rows_df(
        gallery_values=["https://cdn.example.com/1.jpg,https://cdn.example.com/2.jpg"])

    idx_before = CompIndex(df_no_gallery, "المنتج", "id", "متجر الاختبار")
    idx_after = CompIndex(
        df_with_gallery, "المنتج", "id", "متجر الاختبار", gallery_col="image_urls")

    res_before = idx_before.search(
        *_search_args(_PRODUCT), our_pline="اوراق الفضه", top_n=6, our_price=69.0)
    res_after = idx_after.search(
        *_search_args(_PRODUCT), our_pline="اوراق الفضه", top_n=6, our_price=69.0)

    assert len(res_before) == len(res_after) == 1
    b, a = res_before[0], res_after[0]
    # كل حقل موجود قبل التغيير يبقى مطابقاً حرفياً — comp_images وحده يختلف.
    for key in ("name", "score", "price", "product_id", "brand", "size",
                "type", "gender", "competitor", "image_url", "product_url", "thumb"):
        assert a[key] == b[key], f"حقل «{key}» تغيّر بسبب إضافة المعرض — انحدار في المطابقة"
    assert b["comp_images"] == []
    assert a["comp_images"] != []


# ── انتشار المعرض حتى build_card (product_card.py) ──────────────────────────
def test_build_card_propagates_cheapest_competitor_gallery():
    from ui.components.product_card import build_card

    row = {
        "المنتج": "عطر", "السعر": 100,
        "جميع_المنافسين": [
            {"name": "عطر منافس", "price": 90, "competitor": "متجر",
             "image_url": "https://cdn.example.com/main.jpg",
             "comp_images": ["https://cdn.example.com/1.jpg", "https://cdn.example.com/2.jpg"]},
        ],
    }
    card = build_card(row)
    assert card.comp_images == ["https://cdn.example.com/1.jpg", "https://cdn.example.com/2.jpg"]


def test_build_card_gallery_empty_when_absent():
    from ui.components.product_card import build_card

    row = {"المنتج": "عطر", "السعر": 100,
           "جميع_المنافسين": [{"name": "عطر منافس", "price": 90, "competitor": "متجر"}]}
    card = build_card(row)
    assert card.comp_images == []


# ── العرض النهائي: build_comparison_card_html ───────────────────────────────
def test_comparison_card_html_shows_gallery_when_two_or_more_images():
    from ui.components.comparison_card import build_comparison_card_html

    our = {"name": "عطرنا", "price": 100}
    comps = [{
        "name": "منافس أرخص", "price": 80, "store": "س1",
        "image": "https://cdn.example.com/main.jpg",
        "images": ["https://cdn.example.com/1.jpg", "https://cdn.example.com/2.jpg"],
    }]
    html_out = build_comparison_card_html(our, comps)
    assert html_out.count("<img") >= 3  # صورة رئيسية + صورتا معرض على الأقل


def test_comparison_card_html_hides_gallery_when_single_image():
    from ui.components.comparison_card import build_comparison_card_html

    our = {"name": "عطرنا", "price": 100}
    comps = [{"name": "منافس أرخص", "price": 80, "store": "س1",
              "image": "https://cdn.example.com/main.jpg",
              "images": ["https://cdn.example.com/1.jpg"]}]  # صورة واحدة فقط ⇒ لا شريط
    html_out = build_comparison_card_html(our, comps)
    assert html_out.count("<img") == 1  # الصورة الرئيسية فقط، بلا شريط معرض


def test_comparison_card_html_no_gallery_key_unaffected():
    """صفوف بلا مفتاح images إطلاقاً (المسار العربي القديم) تبقى تعمل كالسابق."""
    from ui.components.comparison_card import build_comparison_card_html

    our = {"name": "عطرنا", "price": 100}
    comps = [{"name": "منافس أرخص", "price": 80, "store": "س1",
              "image": "https://cdn.example.com/main.jpg"}]
    html_out = build_comparison_card_html(our, comps)
    assert html_out.count("<img") == 1
