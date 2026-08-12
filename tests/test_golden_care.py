"""tests/test_golden_care.py — القالب الذهبي يتبع نوع المنتج + روابط mahwous حقيقية.

يثبت: العناية تأخذ قالباً ذهبياً (لا بسيطاً) بلا لغة عطور، والعطر يبقى عطرياً،
وكلاهما يستعمل روابط mahwous.com (لا روابط ثابتة قديمة).
"""
from engines.golden_template import build_golden_description, _links_block


def test_care_product_gets_golden_template_no_perfume_language() -> None:
    html = build_golden_description({
        "أسم المنتج": "بوناويل علاج الزيت الساخن بالقمح من شوارزكوف 225مل",
        "الماركة": "Schwarzkopf", "تصنيف_المنتج": "العناية",
    })
    # ذهبي (صندوق ذهبي + شريط أسود) — لا القالب البسيط
    assert "#faf6ec" in html and "#1a1a1a" in html
    # بلا أيّ لغة عطور
    assert not any(x in html for x in ("نقاط النبض", "أو دو بارفيوم", "EDP", "الهرم العطري"))
    # محتوى عناية
    assert "طريقة الاستخدام" in html and "المكوّنات الفعّالة" in html
    # روابط المتجر
    assert "mahwous.com" in html


def test_perfume_stays_perfume_golden_with_store_links() -> None:
    html = build_golden_description({
        "أسم المنتج": "عطر ديور سوفاج للرجال او دو بارفيوم 100مل",
        "الماركة": "Dior", "تصنيف_المنتج": "عطور رجالية",
    })
    assert "#faf6ec" in html
    assert "mahwous.com" in html
    # لا روابط ثابتة قديمة
    assert "taghlieef-hadaya/c1893373528" not in html


def test_links_block_always_has_store_link() -> None:
    # حتى بلا تطابق lookup، يبقى رابط المتجر العام (تدرّج آمن)
    html = _links_block("ماركة غير موجودة xyz", "تصنيف غير موجود xyz")
    assert "https://mahwous.com" in html
    assert "اكتشف المزيد" in html
